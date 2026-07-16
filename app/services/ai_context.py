from __future__ import annotations

import json
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Activity, AiMemory, DailyRecovery, MealLog, RunningShoe, Sleep
from app.services.timezone import local_today, to_local


def build_context(db: Session, user_id: int, message: str, today: date | None = None) -> str:
    settings = get_settings()
    today = today or local_today(settings.app_timezone)
    lower = message.lower()
    sections: list[str] = [
        f"Data local em {settings.app_timezone}: {today.isoformat()}.",
        "Quando o usuario disser hoje, use esta data local.",
    ]
    sections.append(_readiness_context(db, user_id, today))
    if any(token in lower for token in ["atividade", "corrida", "treino", "semana", "strava"]):
        sections.append(_activity_context(db, user_id, today))
    if any(token in lower for token in ["comi", "comer", "refeicao", "alimento", "jantar", "almoco"]):
        sections.append(_meal_context(db, user_id, today))
    if any(token in lower for token in ["tenis", "sapato"]):
        sections.append(_shoe_context(db, user_id))
    memory_text = _memory_context(db, user_id, lower)
    if memory_text:
        sections.append(memory_text)
    return "\n".join(item for item in sections if item)


def _readiness_context(db: Session, user_id: int, day: date) -> str:
    recovery = db.scalar(
        select(DailyRecovery)
        .where(DailyRecovery.user_id == user_id, DailyRecovery.day == day)
        .order_by(DailyRecovery.id.desc())
        .limit(1)
    )
    sleep = db.scalar(
        select(Sleep)
        .where(Sleep.user_id == user_id, Sleep.day == day)
        .order_by(Sleep.id.desc())
        .limit(1)
    )
    payload: dict[str, object] = {"kind": "readiness_today"}
    if recovery:
        payload["recovery"] = {
            "score": recovery.recovery_score,
            "hrv_ms": recovery.hrv_ms,
            "resting_hr": recovery.resting_hr,
            "strain": recovery.daily_strain,
        }
    if sleep:
        payload["sleep"] = {
            "duration_seconds": sleep.sleep_duration_seconds,
            "efficiency_percent": sleep.efficiency_percent,
        }
    if len(payload) == 1:
        payload["missing"] = ["recovery", "sleep"]
    return json.dumps(payload, ensure_ascii=False, default=str)


def _activity_context(db: Session, user_id: int, day: date) -> str:
    start = day - timedelta(days=14)
    rows = db.scalars(
        select(Activity)
        .where(Activity.user_id == user_id, Activity.primary_activity_id.is_(None))
        .where(Activity.started_at >= start)
        .order_by(Activity.started_at.desc())
        .limit(12)
    ).all()
    return json.dumps(
        {
            "kind": "recent_activities",
            "items": [
                {
                    "id": row.id,
                    "type": row.activity_type,
                    "started_at": to_local(row.started_at).isoformat() if row.started_at else None,
                    "duration_seconds": row.total_duration_seconds,
                    "distance_km": round(row.distance_meters / 1000, 2) if row.distance_meters else None,
                    "avg_hr": row.avg_hr,
                }
                for row in rows
            ],
        },
        ensure_ascii=False,
        default=str,
    )


def _meal_context(db: Session, user_id: int, day: date) -> str:
    start = day - timedelta(days=7)
    rows = db.scalars(
        select(MealLog)
        .where(MealLog.user_id == user_id, MealLog.consumed_at >= start)
        .order_by(MealLog.consumed_at.desc())
        .limit(10)
    ).all()
    return json.dumps(
        {
            "kind": "recent_meals",
            "items": [
                {
                    "id": row.id,
                    "consumed_at": to_local(row.consumed_at).isoformat() if row.consumed_at else None,
                    "description": row.description,
                }
                for row in rows
            ],
        },
        ensure_ascii=False,
        default=str,
    )


def _shoe_context(db: Session, user_id: int) -> str:
    rows = db.scalars(
        select(RunningShoe).where(RunningShoe.user_id == user_id).order_by(RunningShoe.status, RunningShoe.name)
    ).all()
    return json.dumps(
        {
            "kind": "running_shoes",
            "items": [
                {
                    "id": row.id,
                    "name": row.name,
                    "status": row.status,
                    "model": row.model,
                    "color": row.color,
                    "condition_notes": row.condition_notes,
                }
                for row in rows
            ],
        },
        ensure_ascii=False,
        default=str,
    )


def _memory_context(db: Session, user_id: int, lower_message: str) -> str:
    query = (
        select(AiMemory)
        .where(
            AiMemory.user_id == user_id,
            AiMemory.active.is_(True),
            AiMemory.confirmed_by_user.is_(True),
        )
        .order_by(AiMemory.updated_at.desc())
    )
    memories = db.scalars(query).all()
    selected = []
    for memory in memories:
        haystack = f"{memory.category} {memory.key} {memory.value_json}".lower()
        if any(token and token in haystack for token in lower_message.split()):
            selected.append(memory)
        elif memory.category in {"alergias", "restricoes", "objetivos"} and any(
            token in lower_message for token in ["comer", "comi", "treino", "objetivo", "alergia"]
        ):
            selected.append(memory)
        if len(selected) >= 8:
            break
    if not selected:
        return ""
    return json.dumps(
        {
            "kind": "confirmed_memories",
            "items": [
                {
                    "category": memory.category,
                    "key": memory.key,
                    "value": json.loads(memory.value_json),
                }
                for memory in selected
            ],
        },
        ensure_ascii=False,
        default=str,
    )
