from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from threading import Thread

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services import sync_locks
from app.models import (
    Activity,
    AiAuditLog,
    AiMemory,
    DailyRecovery,
    DailySummary,
    DataSource,
    IntegrationState,
    ManualShoeUsage,
    MealItem,
    MealLog,
    OAuthCredential,
    PlannedActivity,
    RunningShoe,
    ShoeActivityLink,
    Sleep,
    SubjectiveCheckin,
    SyncLog,
)
from app.services.dashboard_api import data_quality_payload
from app.services.timezone import local_today, to_local, utc_now


class ToolError(ValueError):
    pass


class AmbiguousToolRequest(ToolError):
    def __init__(self, message: str, candidates: list[dict[str, object]]):
        super().__init__(message)
        self.candidates = candidates


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    message: str
    data: dict[str, object]


logger = logging.getLogger(__name__)


def get_recovery(db: Session, user_id: int, day: date) -> ToolResult:
    recovery = db.scalar(
        select(DailyRecovery)
        .where(DailyRecovery.user_id == user_id, DailyRecovery.day == day)
        .order_by(DailyRecovery.id.desc())
        .limit(1)
    )
    if not recovery:
        return _missing("Recovery", "whoop", ["recovery_score", "hrv_ms", "resting_hr"])
    return ToolResult(
        True,
        "Recovery encontrado.",
        {
            "recovery_score": recovery.recovery_score,
            "hrv_ms": recovery.hrv_ms,
            "resting_hr": recovery.resting_hr,
            "daily_strain": recovery.daily_strain,
            "source": "whoop",
            "updated_at": day.isoformat(),
            "quality": "completa" if recovery.recovery_score is not None else "parcial",
            "missing_fields": _missing_fields(
                recovery,
                ["recovery_score", "hrv_ms", "resting_hr", "daily_strain"],
            ),
        },
    )


def get_sleep(db: Session, user_id: int, day: date) -> ToolResult:
    sleep = db.scalar(
        select(Sleep)
        .where(Sleep.user_id == user_id, Sleep.day == day)
        .order_by(Sleep.id.desc())
        .limit(1)
    )
    if not sleep:
        return _missing("Sono", "whoop", ["sleep_duration_seconds", "efficiency_percent"])
    return ToolResult(
        True,
        "Sono encontrado.",
        {
            "duration_seconds": sleep.sleep_duration_seconds,
            "efficiency_percent": sleep.efficiency_percent,
            "started_at": _iso(sleep.started_at),
            "ended_at": _iso(sleep.ended_at),
            "source": "whoop",
            "updated_at": day.isoformat(),
            "quality": "completa" if sleep.sleep_duration_seconds else "parcial",
            "missing_fields": _missing_fields(sleep, ["sleep_duration_seconds", "efficiency_percent"]),
        },
    )


def get_daily_summary(db: Session, user_id: int, day: date) -> ToolResult:
    summary = db.scalar(
        select(DailySummary)
        .where(DailySummary.user_id == user_id, DailySummary.day <= day)
        .order_by(DailySummary.day.desc(), DailySummary.created_at.desc())
        .limit(1)
    )
    if not summary:
        return _missing("Analise diaria", "sistema", ["summary_markdown"])
    return ToolResult(
        True,
        "Analise diaria encontrada.",
        {
            "day": summary.day.isoformat(),
            "classification": summary.classification,
            "data_quality": summary.data_quality,
            "summary": summary.summary_markdown,
            "source": "sistema",
            "updated_at": _iso(summary.created_at),
            "missing_fields": [],
        },
    )


def get_recent_activities(
    db: Session,
    user_id: int,
    start_day: date,
    end_day: date,
    activity_type: str | None = None,
) -> ToolResult:
    query = select(Activity).where(
        Activity.user_id == user_id,
        Activity.primary_activity_id.is_(None),
        Activity.started_at >= datetime.combine(start_day, datetime.min.time()),
        Activity.started_at <= datetime.combine(end_day + timedelta(days=1), datetime.min.time()),
    )
    if activity_type:
        query = query.where(Activity.activity_type.ilike(f"%{activity_type}%"))
    rows = db.scalars(query.order_by(Activity.started_at.desc()).limit(20)).all()
    return ToolResult(
        True,
        "Atividades encontradas." if rows else "Nao tenho esse dado registrado.",
        {
            "activities": [
                {
                    "id": item.id,
                    "type": item.activity_type,
                    "started_at": _iso(item.started_at),
                    "duration_seconds": item.total_duration_seconds,
                    "distance_km": round((item.distance_meters or 0) / 1000, 2)
                    if item.distance_meters
                    else None,
                }
                for item in rows
            ],
            "source": "backend",
            "quality": "completa" if rows else "insuficiente",
            "missing_fields": [] if rows else ["activities"],
        },
    )


def get_activity_details(db: Session, user_id: int, activity_id: int) -> ToolResult:
    activity = db.get(Activity, activity_id)
    if not activity or activity.user_id != user_id:
        return _missing("Atividade", "backend", ["activity"])
    return ToolResult(
        True,
        "Atividade encontrada.",
        {
            "id": activity.id,
            "type": activity.activity_type,
            "started_at": _iso(activity.started_at),
            "duration_seconds": activity.total_duration_seconds,
            "distance_km": round((activity.distance_meters or 0) / 1000, 2)
            if activity.distance_meters
            else None,
            "avg_hr": activity.avg_hr,
            "source": "backend",
            "quality": "parcial",
            "missing_fields": _missing_fields(activity, ["distance_meters", "avg_hr"]),
        },
    )


def get_metric_history(
    db: Session,
    user_id: int,
    metric: str,
    start_day: date,
    end_day: date,
    source: str | None = None,
) -> ToolResult:
    rows: list[dict[str, object]] = []
    if metric in {"recovery", "hrv", "resting_hr", "strain"}:
        field = {
            "recovery": "recovery_score",
            "hrv": "hrv_ms",
            "resting_hr": "resting_hr",
            "strain": "daily_strain",
        }[metric]
        for item in db.scalars(
            select(DailyRecovery).where(
                DailyRecovery.user_id == user_id,
                DailyRecovery.day >= start_day,
                DailyRecovery.day <= end_day,
            )
        ):
            rows.append({"day": item.day.isoformat(), "value": getattr(item, field), "source": source or "whoop"})
    elif metric == "sleep":
        for item in db.scalars(
            select(Sleep).where(
                Sleep.user_id == user_id,
                Sleep.day >= start_day,
                Sleep.day <= end_day,
            )
        ):
            rows.append({"day": item.day.isoformat(), "value": item.sleep_duration_seconds, "source": source or "whoop"})
    elif metric in {"distance", "activity_duration"}:
        activities = get_recent_activities(db, user_id, start_day, end_day).data["activities"]
        for item in activities:
            rows.append(
                {
                    "day": str(item["started_at"])[:10],
                    "value": item["distance_km"] if metric == "distance" else item["duration_seconds"],
                    "source": source or "activities",
                }
            )
    else:
        raise ToolError("Metrica nao suportada.")
    return ToolResult(
        True,
        "Historico encontrado." if rows else "Nao tenho esse dado registrado.",
        {
            "metric": metric,
            "rows": rows,
            "source": source or "backend",
            "quality": "completa" if rows else "insuficiente",
            "missing_fields": [] if rows else [metric],
        },
    )


def get_user_checkin(db: Session, user_id: int, day: date) -> ToolResult:
    checkin = db.scalar(
        select(SubjectiveCheckin).where(
            SubjectiveCheckin.user_id == user_id,
            SubjectiveCheckin.day == day,
        )
    )
    if not checkin:
        return _missing("Check-in", "usuario", ["subjective_checkin"])
    return ToolResult(
        True,
        "Check-in encontrado.",
        {
            "day": day.isoformat(),
            "energy": checkin.energy,
            "sleep_quality": checkin.sleep_quality,
            "muscle_soreness": checkin.muscle_soreness,
            "mood": checkin.mood,
            "red_flags": checkin.red_flags,
            "source": "usuario",
            "quality": "parcial",
            "missing_fields": _missing_fields(checkin, ["energy", "sleep_quality", "muscle_soreness"]),
        },
    )


def get_today_meals(db: Session, user_id: int) -> ToolResult:
    today = local_today(get_settings().app_timezone)
    return get_meal_history(db, user_id, today, today)


def get_meal_history(db: Session, user_id: int, start_day: date, end_day: date) -> ToolResult:
    start = datetime.combine(start_day, datetime.min.time())
    end = datetime.combine(end_day + timedelta(days=1), datetime.min.time())
    meals = db.scalars(
        select(MealLog)
        .where(MealLog.user_id == user_id, MealLog.consumed_at >= start, MealLog.consumed_at < end)
        .order_by(MealLog.consumed_at.desc())
    ).all()
    return ToolResult(
        True,
        "Refeicoes encontradas." if meals else "Nao tenho esse dado registrado.",
        {
            "meals": [_meal_payload(db, meal) for meal in meals],
            "source": "meal_logs",
            "quality": "completa" if meals else "insuficiente",
            "missing_fields": [] if meals else ["meal_logs"],
        },
    )


def create_meal_log(
    db: Session,
    user_id: int,
    description: str,
    consumed_at: datetime | None = None,
    meal_type: str = "refeicao",
    items: list[dict[str, object]] | None = None,
    source: str = "ai_tool",
) -> ToolResult:
    if not description.strip():
        raise ToolError("Descricao da refeicao obrigatoria.")
    meal = MealLog(
        user_id=user_id,
        consumed_at=consumed_at or utc_now(),
        meal_type=meal_type,
        description=description.strip(),
        source=source,
        confirmed=True,
    )
    db.add(meal)
    db.flush()
    for item in items or []:
        name = str(item.get("name") or "").strip()
        if name:
            db.add(
                MealItem(
                    meal_log_id=meal.id,
                    name=name,
                    quantity=_float_or_none(item.get("quantity")),
                    unit=str(item.get("unit")) if item.get("unit") else None,
                    calories=_float_or_none(item.get("calories")),
                    protein_g=_float_or_none(item.get("protein_g")),
                    carbohydrate_g=_float_or_none(item.get("carbohydrate_g")),
                    fat_g=_float_or_none(item.get("fat_g")),
                    nutrition_source=str(item.get("nutrition_source"))
                    if item.get("nutrition_source")
                    else None,
                    confidence=_float_or_none(item.get("confidence")),
                )
            )
    _audit(db, user_id, "create_meal_log", "meal_log", meal.id, {"description": description})
    db.commit()
    return ToolResult(True, "Refeicao registrada.", {"meal": _meal_payload(db, meal)})


def update_meal_log(db: Session, user_id: int, meal_id: int, **changes) -> ToolResult:
    meal = _owned(db, MealLog, user_id, meal_id)
    for field in ["description", "meal_type", "notes"]:
        if field in changes and changes[field] is not None:
            setattr(meal, field, str(changes[field]))
    _audit(db, user_id, "update_meal_log", "meal_log", meal.id, changes)
    db.commit()
    return ToolResult(True, "Refeicao atualizada.", {"meal": _meal_payload(db, meal)})


def delete_meal_log(db: Session, user_id: int, meal_id: int) -> ToolResult:
    meal = _owned(db, MealLog, user_id, meal_id)
    _audit(db, user_id, "delete_meal_log", "meal_log", meal.id, {})
    db.delete(meal)
    db.commit()
    return ToolResult(True, "Refeicao excluida.", {"meal_id": meal_id})


def get_user_preferences(db: Session, user_id: int, category: str | None = None) -> ToolResult:
    query = select(AiMemory).where(
        AiMemory.user_id == user_id,
        AiMemory.active.is_(True),
        AiMemory.confirmed_by_user.is_(True),
    )
    if category:
        query = query.where(AiMemory.category == category)
    memories = db.scalars(query.order_by(AiMemory.updated_at.desc())).all()
    return ToolResult(
        True,
        "Memorias encontradas." if memories else "Nao tenho esse dado registrado.",
        {
            "memories": [_memory_payload(memory) for memory in memories],
            "source": "ai_memories",
            "quality": "completa" if memories else "insuficiente",
            "missing_fields": [] if memories else ["memories"],
        },
    )


def get_user_goals(db: Session, user_id: int) -> ToolResult:
    return get_user_preferences(db, user_id, "objetivos")


def save_confirmed_memory(
    db: Session,
    user_id: int,
    category: str,
    key: str,
    value: object,
    source: str = "user_confirmed",
) -> ToolResult:
    if not category or not key:
        raise ToolError("Categoria e chave sao obrigatorias.")
    existing = db.scalar(
        select(AiMemory).where(
            AiMemory.user_id == user_id,
            AiMemory.category == category,
            AiMemory.key == key,
        )
    )
    memory = existing or AiMemory(
        user_id=user_id,
        category=category,
        key=key,
        value_json="{}",
        confirmed_by_user=True,
        active=True,
    )
    memory.value_json = json.dumps(value, ensure_ascii=False)
    memory.source = source
    memory.confidence = 1.0
    memory.confirmed_by_user = True
    memory.active = True
    memory.deleted_at = None
    db.add(memory)
    db.flush()
    _audit(db, user_id, "save_confirmed_memory", "ai_memory", memory.id, {"category": category, "key": key})
    db.commit()
    return ToolResult(True, "Memoria salva.", {"memory": _memory_payload(memory)})


def update_memory(db: Session, user_id: int, memory_id: int, value: object) -> ToolResult:
    memory = _owned(db, AiMemory, user_id, memory_id)
    memory.value_json = json.dumps(value, ensure_ascii=False)
    memory.confirmed_by_user = True
    memory.active = True
    _audit(db, user_id, "update_memory", "ai_memory", memory.id, {"value": value})
    db.commit()
    return ToolResult(True, "Memoria atualizada.", {"memory": _memory_payload(memory)})


def delete_memory(db: Session, user_id: int, memory_id: int) -> ToolResult:
    memory = _owned(db, AiMemory, user_id, memory_id)
    memory.active = False
    memory.deleted_at = utc_now()
    _audit(db, user_id, "delete_memory", "ai_memory", memory.id, {})
    db.commit()
    return ToolResult(True, "Memoria apagada.", {"memory_id": memory_id})


def get_shoes(db: Session, user_id: int, status: str | None = None) -> ToolResult:
    query = select(RunningShoe).where(RunningShoe.user_id == user_id)
    if status:
        query = query.where(RunningShoe.status == status)
    shoes = db.scalars(query.order_by(RunningShoe.status, RunningShoe.name)).all()
    return ToolResult(
        True,
        "Tenis encontrados." if shoes else "Nao tenho esse dado registrado.",
        {
            "shoes": [_shoe_payload(db, shoe) for shoe in shoes],
            "source": "running_shoes",
            "quality": "completa" if shoes else "insuficiente",
            "missing_fields": [] if shoes else ["running_shoes"],
        },
    )


def get_shoe_details(db: Session, user_id: int, shoe_id: int) -> ToolResult:
    shoe = _owned(db, RunningShoe, user_id, shoe_id)
    return ToolResult(True, "Tenis encontrado.", {"shoe": _shoe_payload(db, shoe)})


def create_shoe(db: Session, user_id: int, name: str, **attrs) -> ToolResult:
    if not name.strip():
        raise ToolError("Nome do tenis obrigatorio.")
    shoe = RunningShoe(
        user_id=user_id,
        name=name.strip(),
        brand=_optional(attrs.get("brand")),
        model=_optional(attrs.get("model")),
        color=_optional(attrs.get("color")),
        initial_distance_km=_float_or_none(attrs.get("initial_distance_km")) or 0.0,
        expected_min_km=_float_or_none(attrs.get("expected_min_km")),
        expected_max_km=_float_or_none(attrs.get("expected_max_km")),
        preferred_uses_json=_json_or_none(attrs.get("preferred_uses")),
        surfaces_json=_json_or_none(attrs.get("surfaces")),
        status="active",
        condition_notes=_optional(attrs.get("condition_notes")),
    )
    db.add(shoe)
    db.flush()
    _audit(db, user_id, "create_shoe", "running_shoe", shoe.id, {"name": name})
    db.commit()
    return ToolResult(True, "Tenis cadastrado.", {"shoe": _shoe_payload(db, shoe)})


def update_shoe(db: Session, user_id: int, shoe_id: int, **attrs) -> ToolResult:
    shoe = _owned(db, RunningShoe, user_id, shoe_id)
    for field in [
        "name",
        "brand",
        "model",
        "color",
        "status",
        "condition_notes",
    ]:
        if field in attrs and attrs[field] is not None:
            setattr(shoe, field, str(attrs[field]))
    for field in ["initial_distance_km", "expected_min_km", "expected_max_km"]:
        if field in attrs and attrs[field] is not None:
            setattr(shoe, field, _float_or_none(attrs[field]))
    _audit(db, user_id, "update_shoe", "running_shoe", shoe.id, attrs)
    db.commit()
    return ToolResult(True, "Tenis atualizado.", {"shoe": _shoe_payload(db, shoe)})


def retire_shoe(db: Session, user_id: int, shoe_id: int, notes: str | None = None) -> ToolResult:
    shoe = _owned(db, RunningShoe, user_id, shoe_id)
    shoe.status = "retired"
    shoe.retired_at = utc_now()
    if notes:
        shoe.condition_notes = notes
    _audit(db, user_id, "retire_shoe", "running_shoe", shoe.id, {"notes": notes})
    db.commit()
    return ToolResult(True, "Tenis aposentado.", {"shoe": _shoe_payload(db, shoe)})


def associate_shoe_with_activity(
    db: Session,
    user_id: int,
    shoe_id: int,
    activity_id: int,
    confidence: float = 1.0,
) -> ToolResult:
    shoe = _owned(db, RunningShoe, user_id, shoe_id)
    activity = _owned(db, Activity, user_id, activity_id)
    existing = db.scalar(
        select(ShoeActivityLink).where(
            ShoeActivityLink.shoe_id == shoe.id,
            ShoeActivityLink.activity_id == activity.id,
        )
    )
    if existing:
        return ToolResult(True, "Associacao ja existia.", {"shoe": _shoe_payload(db, shoe)})
    link = ShoeActivityLink(
        shoe_id=shoe.id,
        activity_id=activity.id,
        distance_km=round((activity.distance_meters or 0) / 1000, 2),
        used_at=activity.started_at,
        source="ai_tool",
        confidence=confidence,
    )
    db.add(link)
    db.flush()
    _audit(
        db,
        user_id,
        "associate_shoe_with_activity",
        "shoe_activity_link",
        link.id,
        {"shoe_id": shoe_id, "activity_id": activity_id},
    )
    db.commit()
    return ToolResult(True, "Tenis associado a atividade.", {"shoe": _shoe_payload(db, shoe)})


def create_manual_shoe_usage(
    db: Session,
    user_id: int,
    shoe_id: int,
    usage_date: date,
    distance_km: float,
    activity_type: str = "running",
    notes: str | None = None,
) -> ToolResult:
    shoe = _owned(db, RunningShoe, user_id, shoe_id)
    manual = ManualShoeUsage(
        user_id=user_id,
        date=usage_date,
        distance_km=distance_km,
        activity_type=activity_type,
        notes=notes,
    )
    db.add(manual)
    db.flush()
    link = ShoeActivityLink(
        shoe_id=shoe.id,
        manual_usage_id=manual.id,
        distance_km=distance_km,
        used_at=datetime.combine(usage_date, datetime.min.time()),
        source="manual",
        confidence=1.0,
    )
    db.add(link)
    db.flush()
    _audit(db, user_id, "create_manual_shoe_usage", "manual_shoe_usage", manual.id, {"shoe_id": shoe_id})
    db.commit()
    return ToolResult(True, "Uso manual registrado.", {"shoe": _shoe_payload(db, shoe)})


def get_shoe_usage_history(db: Session, user_id: int, shoe_id: int) -> ToolResult:
    shoe = _owned(db, RunningShoe, user_id, shoe_id)
    links = db.scalars(
        select(ShoeActivityLink)
        .where(ShoeActivityLink.shoe_id == shoe.id)
        .order_by(ShoeActivityLink.used_at.desc())
    ).all()
    return ToolResult(
        True,
        "Historico encontrado." if links else "Nao tenho esse dado registrado.",
        {
            "usages": [
                {
                    "id": link.id,
                    "activity_id": link.activity_id,
                    "manual_usage_id": link.manual_usage_id,
                    "distance_km": link.distance_km,
                    "used_at": _iso(link.used_at),
                    "source": link.source,
                }
                for link in links
            ],
            "shoe": _shoe_payload(db, shoe),
        },
    )


def get_shoe_recommendation_context(
    db: Session,
    user_id: int,
    planned_activity: dict[str, object] | None = None,
) -> ToolResult:
    shoes = db.scalars(
        select(RunningShoe).where(RunningShoe.user_id == user_id, RunningShoe.status == "active")
    ).all()
    candidates = []
    for shoe in shoes:
        payload = _shoe_payload(db, shoe)
        reasons = ["ativo"]
        remaining = payload.get("remaining_max_km")
        if remaining is None:
            reasons.append("estimativa ainda nao configurada")
        candidates.append({"shoe": payload, "reasons": reasons, "score": 1 if remaining is None or remaining > 0 else 0})
    return ToolResult(
        True,
        "Candidatos encontrados." if candidates else "Nao tenho esse dado registrado.",
        {
            "planned_activity": planned_activity or {},
            "candidates": candidates,
            "source": "running_shoes",
            "quality": "parcial" if candidates else "insuficiente",
            "missing_fields": [] if candidates else ["running_shoes"],
        },
    )


def save_daily_note(db: Session, user_id: int, day: date, note: str) -> ToolResult:
    from app.models import DailyNote

    row = DailyNote(user_id=user_id, day=day, note=note, source="ai_tool")
    db.add(row)
    db.flush()
    _audit(db, user_id, "save_daily_note", "daily_note", row.id, {"day": day.isoformat()})
    db.commit()
    return ToolResult(True, "Nota diaria salva.", {"note_id": row.id})


def save_planned_activity(
    db: Session,
    user_id: int,
    planned_for: date,
    activity_type: str,
    distance_km: float | None = None,
    intensity: str | None = None,
    surface: str | None = None,
    notes: str | None = None,
) -> ToolResult:
    row = PlannedActivity(
        user_id=user_id,
        planned_for=planned_for,
        activity_type=activity_type,
        distance_km=distance_km,
        intensity=intensity,
        surface=surface,
        notes=notes,
    )
    db.add(row)
    db.flush()
    _audit(db, user_id, "save_planned_activity", "planned_activity", row.id, {"activity_type": activity_type})
    db.commit()
    return ToolResult(True, "Treino planejado salvo.", {"planned_activity_id": row.id})


def get_data_quality(db: Session, user_id: int, day: date) -> ToolResult:
    from app.services.dashboard_api import build_filters

    filters = build_filters(day=day, period="today")
    return ToolResult(True, "Qualidade consultada.", data_quality_payload(db, user_id, filters))


def get_sync_status(db: Session, user_id: int, source: str = "all") -> ToolResult:
    sources = _sync_sources(source)
    statuses = [_sync_status_payload(db, user_id, item) for item in sources]
    return ToolResult(
        True,
        "Status de sincronizacao consultado.",
        {"sources": statuses, "quality": "operacional", "source": "sync_logs"},
    )


def sync_integrations(db: Session, user_id: int, source: str = "all") -> ToolResult:
    sources = _sync_sources(source)
    statuses = []
    for item in sources:
        status = _sync_status_payload(db, user_id, item)
        if status["state"] == "not_connected":
            statuses.append(status)
            continue
        if status["state"] == "running":
            statuses.append(status)
            continue
        if _start_background_sync(user_id, item):
            status["state"] = "started"
            status["message"] = f"Sincronizacao {item.upper()} iniciada."
        else:
            status["state"] = "running"
            status["message"] = f"Sincronizacao {item.upper()} ja esta em andamento."
        statuses.append(status)
    return ToolResult(
        True,
        "Sincronizacao solicitada.",
        {"sources": statuses, "quality": "operacional", "source": "sync_background"},
    )


def find_activity_candidates(
    db: Session,
    user_id: int,
    target_day: date,
    distance_km: float | None = None,
    activity_type: str | None = None,
) -> list[Activity]:
    start = datetime.combine(target_day, datetime.min.time())
    end = start + timedelta(days=1)
    query = select(Activity).where(
        Activity.user_id == user_id,
        Activity.started_at >= start,
        Activity.started_at < end,
        Activity.primary_activity_id.is_(None),
    )
    if activity_type:
        query = query.where(Activity.activity_type.ilike(f"%{activity_type}%"))
    candidates = list(db.scalars(query))
    if distance_km is not None:
        candidates = [
            item
            for item in candidates
            if item.distance_meters is not None and abs((item.distance_meters / 1000) - distance_km) <= 0.5
        ]
    return candidates


def get_activities_without_shoe(
    db: Session, user_id: int, day: date, activity_type: str | None = None
) -> ToolResult:
    candidates = find_activity_candidates(db, user_id, day, activity_type=activity_type)
    if not candidates:
        return ToolResult(True, "Nenhuma atividade encontrada nesse dia.", {"activities": [], "source": "activities"})
    ids = [item.id for item in candidates]
    linked_ids = {
        link.activity_id
        for link in db.scalars(select(ShoeActivityLink).where(ShoeActivityLink.activity_id.in_(ids)))
    }
    unlinked = [item for item in candidates if item.id not in linked_ids]
    return ToolResult(
        True,
        "Atividades sem tenis associado encontradas." if unlinked else "Todas as atividades desse dia ja tem tenis associado.",
        {
            "activities": [
                {
                    "id": item.id,
                    "activity_type": item.activity_type,
                    "distance_km": round(item.distance_meters / 1000, 2) if item.distance_meters else None,
                    "started_at": _iso(item.started_at),
                }
                for item in unlinked
            ],
            "source": "activities",
        },
    )


def _sync_sources(source: str) -> list[str]:
    normalized = (source or "all").strip().lower()
    if normalized == "all":
        return ["strava", "whoop"]
    if normalized in {"strava", "whoop"}:
        return [normalized]
    raise ToolError("Fonte de sincronizacao nao suportada.")


def _sync_status_payload(db: Session, user_id: int, source_name: str) -> dict[str, object]:
    source = db.scalar(select(DataSource).where(DataSource.name == source_name))
    if not source:
        return {"source": source_name, "state": "not_connected", "message": f"{source_name.upper()} nao conectado."}
    credential = db.scalar(
        select(OAuthCredential).where(
            OAuthCredential.user_id == user_id,
            OAuthCredential.data_source_id == source.id,
        )
    )
    state = db.scalar(
        select(IntegrationState).where(
            IntegrationState.user_id == user_id,
            IntegrationState.data_source_id == source.id,
        )
    )
    last_log = db.scalar(
        select(SyncLog)
        .where(SyncLog.data_source_id == source.id, SyncLog.action == f"{source_name}_sync")
        .order_by(SyncLog.started_at.desc(), SyncLog.id.desc())
        .limit(1)
    )
    if not credential:
        sync_state = "not_connected"
        message = f"{source_name.upper()} nao conectado."
    elif last_log and last_log.status == "running" and last_log.finished_at is None:
        sync_state = "running"
        message = f"Sincronizacao {source_name.upper()} ja esta em andamento."
    else:
        sync_state = "ready"
        message = f"{source_name.upper()} pronto para sincronizar."
    return {
        "source": source_name,
        "state": sync_state,
        "message": message,
        "integration_status": state.status if state else None,
        "last_synced_at": _iso(state.last_synced_at) if state else None,
        "last_log_status": last_log.status if last_log else None,
        "last_log_started_at": _iso(last_log.started_at) if last_log else None,
        "last_log_finished_at": _iso(last_log.finished_at) if last_log else None,
    }


def _start_background_sync(user_id: int, source_name: str) -> bool:
    if not sync_locks.try_start(source_name):
        return False
    thread = Thread(
        target=_run_background_sync,
        args=(user_id, source_name),
        name=f"ai-sync-{source_name}",
        daemon=True,
    )
    thread.start()
    return True


def _run_background_sync(user_id: int, source_name: str) -> None:
    from app.db import SessionLocal
    from app.services.sync import sync_strava
    from app.services.whoop_sync import sync_whoop

    with SessionLocal() as sync_db:
        try:
            if source_name == "strava":
                sync_strava(sync_db, user_id)
            elif source_name == "whoop":
                sync_whoop(sync_db, user_id)
        except Exception:
            logger.exception("Sincronizacao solicitada pela IA falhou para %s.", source_name)
        finally:
            sync_locks.finish(source_name)


def resolve_single_shoe(db: Session, user_id: int, text: str) -> RunningShoe:
    term = text.strip().lower()
    shoes = db.scalars(
        select(RunningShoe).where(
            RunningShoe.user_id == user_id,
            RunningShoe.status != "retired",
        )
    ).all()
    matches = [
        shoe
        for shoe in shoes
        if term in shoe.name.lower()
        or (shoe.color and term in shoe.color.lower())
        or (shoe.model and term in shoe.model.lower())
    ]
    if not matches:
        raise ToolError("Nao tenho esse tenis registrado.")
    if len(matches) > 1:
        raise AmbiguousToolRequest(
            "Encontrei mais de um tenis compativel. Escolha qual deles.",
            [{"id": shoe.id, "name": shoe.name, "color": shoe.color, "model": shoe.model} for shoe in matches],
        )
    return matches[0]


def _shoe_payload(db: Session, shoe: RunningShoe) -> dict[str, object]:
    links = db.scalars(select(ShoeActivityLink).where(ShoeActivityLink.shoe_id == shoe.id)).all()
    usage_km = sum((link.distance_km or 0) for link in links)
    total = round((shoe.initial_distance_km or 0) + usage_km, 2)
    remaining_min = round(shoe.expected_min_km - total, 2) if shoe.expected_min_km is not None else None
    remaining_max = round(shoe.expected_max_km - total, 2) if shoe.expected_max_km is not None else None
    last_use = max((link.used_at for link in links), default=None)
    activity_links = [link for link in links if link.activity_id]
    manual_links = [link for link in links if link.manual_usage_id]
    unknown_distance_activity_count = len(
        [link for link in activity_links if link.distance_km is None or link.distance_km <= 0]
    )
    recent_usages = []
    for link in sorted(links, key=lambda item: item.used_at or datetime.min, reverse=True)[:8]:
        activity = db.get(Activity, link.activity_id) if link.activity_id else None
        recent_usages.append(
            {
                "activity_id": link.activity_id,
                "manual_usage_id": link.manual_usage_id,
                "type": activity.activity_type if activity else "manual",
                "used_at": _iso(link.used_at),
                "distance_km": link.distance_km if link.distance_km and link.distance_km > 0 else None,
                "duration_seconds": activity.total_duration_seconds if activity else None,
                "source": link.source,
            }
        )
    return {
        "id": shoe.id,
        "name": shoe.name,
        "brand": shoe.brand,
        "model": shoe.model,
        "color": shoe.color,
        "status": shoe.status,
        "total_distance_km": total,
        "known_distance_km": round(usage_km, 2),
        "initial_distance_km": shoe.initial_distance_km or 0,
        "usage_count": len(links),
        "activity_count": len(activity_links),
        "manual_usage_count": len(manual_links),
        "unknown_distance_activity_count": unknown_distance_activity_count,
        "last_use": _iso(last_use),
        "expected_min_km": shoe.expected_min_km,
        "expected_max_km": shoe.expected_max_km,
        "remaining_min_km": remaining_min,
        "remaining_max_km": remaining_max,
        "estimate_status": "configurada" if shoe.expected_min_km or shoe.expected_max_km else "Estimativa ainda nao configurada.",
        "condition_notes": shoe.condition_notes,
        "photo_url": f"/api/shoes/{shoe.id}/photo" if shoe.photo_path else None,
        "recent_usages": recent_usages,
    }


def _meal_payload(db: Session, meal: MealLog) -> dict[str, object]:
    items = db.scalars(select(MealItem).where(MealItem.meal_log_id == meal.id)).all()
    return {
        "id": meal.id,
        "consumed_at": _iso(meal.consumed_at),
        "meal_type": meal.meal_type,
        "description": meal.description,
        "confirmed": meal.confirmed,
        "items": [
            {
                "id": item.id,
                "name": item.name,
                "quantity": item.quantity,
                "unit": item.unit,
                "calories": item.calories,
                "protein_g": item.protein_g,
                "carbohydrate_g": item.carbohydrate_g,
                "fat_g": item.fat_g,
                "nutrition_source": item.nutrition_source,
            }
            for item in items
        ],
    }


def _memory_payload(memory: AiMemory) -> dict[str, object]:
    return {
        "id": memory.id,
        "category": memory.category,
        "key": memory.key,
        "value": json.loads(memory.value_json),
        "source": memory.source,
        "confidence": memory.confidence,
        "confirmed_by_user": memory.confirmed_by_user,
        "active": memory.active,
        "updated_at": _iso(memory.updated_at),
    }


def _owned(db: Session, model, user_id: int, row_id: int):
    row = db.get(model, row_id)
    if not row or getattr(row, "user_id", user_id) != user_id:
        raise ToolError("Registro nao encontrado.")
    return row


def _audit(
    db: Session,
    user_id: int,
    tool_name: str,
    target_type: str,
    target_id: int | None,
    payload: dict[str, object],
) -> None:
    db.add(
        AiAuditLog(
            user_id=user_id,
            tool_name=tool_name,
            target_type=target_type,
            target_id=target_id,
            action="write",
            payload_json=json.dumps(payload, ensure_ascii=False, default=str),
        )
    )


def _missing(name: str, source: str, fields: list[str]) -> ToolResult:
    return ToolResult(
        True,
        "Nao tenho esse dado registrado.",
        {"name": name, "source": source, "quality": "insuficiente", "missing_fields": fields},
    )


def _missing_fields(row, fields: list[str]) -> list[str]:
    return [field for field in fields if getattr(row, field) is None]


def _float_or_none(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _optional(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _json_or_none(value: object) -> str | None:
    if value in (None, ""):
        return None
    return json.dumps(value, ensure_ascii=False)


def _iso(value: datetime | None) -> str | None:
    return to_local(value).isoformat() if value else None
