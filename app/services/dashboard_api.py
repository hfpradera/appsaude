from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import mean

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Activity,
    ActivitySourceLink,
    DailyRecovery,
    DailySummary,
    DataSource,
    IntegrationState,
    Sleep,
    SyncLog,
)
from app.services import reports
from app.services.timezone import seconds_to_human, to_local

DEFAULT_ACTIVITY_SOURCES = {"garmin_fit", "strava", "manual"}
SOURCE_FILTERS = {
    "strava": {"strava"},
    "whoop": {"whoop"},
    "garmin_fit": {"garmin_fit", "fit", "manual"},
}
ACTIVITY_TYPES = {
    "corrida": {"run", "running", "corrida"},
    "caminhada": {"walk", "walking", "caminhada"},
    "ciclismo": {"bike", "ride", "cycling", "ciclismo"},
    "musculacao": {"strength", "weighttraining", "musculacao"},
    "natacao": {"swim", "swimming", "natacao"},
}


@dataclass(frozen=True)
class DashboardFilters:
    day: date
    start_day: date
    end_day: date
    period_label: str
    source: str = "all"
    activity_type: str = "all"
    status: str = "all"
    search: str = ""
    page: int = 1
    page_size: int = 25


def build_filters(
    *,
    day: date | None = None,
    period: str = "today",
    start_date: date | None = None,
    end_date: date | None = None,
    source: str = "all",
    activity_type: str = "all",
    status: str = "all",
    search: str = "",
    page: int = 1,
    page_size: int = 25,
) -> DashboardFilters:
    selected_day = day or date.today()
    end_day = end_date or selected_day
    if start_date and end_date:
        start_day = start_date
        label = f"{start_day.isoformat()} a {end_day.isoformat()}"
    elif period == "yesterday":
        start_day = end_day = selected_day - timedelta(days=1)
        selected_day = end_day
        label = "Ontem"
    elif period in {"7", "14", "30", "90"}:
        span = int(period)
        start_day = end_day - timedelta(days=span - 1)
        label = f"Ultimos {span} dias"
    else:
        start_day = end_day
        label = "Hoje"
    if start_day > end_day:
        start_day, end_day = end_day, start_day
    return DashboardFilters(
        day=selected_day,
        start_day=start_day,
        end_day=end_day,
        period_label=label,
        source=source or "all",
        activity_type=activity_type or "all",
        status=status or "all",
        search=(search or "").strip(),
        page=max(page, 1),
        page_size=min(max(page_size, 1), 100),
    )


def dashboard_payload(db: Session, user_id: int, filters: DashboardFilters) -> dict[str, object]:
    summary = summary_payload(db, user_id, filters)
    return {
        "filters": _filters_payload(filters),
        "summary": summary,
        "metrics": metrics_payload(db, user_id, filters),
        "trend": trend_payload(db, user_id, filters),
        "calendar": calendar_payload(db, user_id, filters.day),
        "timeline": timeline_payload(db, user_id, filters),
        "activities": activities_payload(db, user_id, filters),
        "data_quality": data_quality_payload(db, user_id, filters),
        "analysis": analysis_payload(db, user_id, filters.day),
    }


def summary_payload(db: Session, user_id: int, filters: DashboardFilters) -> dict[str, object]:
    data = reports.dashboard(db, user_id, filters.day)
    recovery = data["recovery"]
    sleep = data["sleep"]
    classification = data["classification"]
    score = _daily_score(recovery, sleep)
    return {
        "day": filters.day.isoformat(),
        "period": filters.period_label,
        "classification": _classification_label(classification.label),
        "classification_key": classification.label,
        "score": score,
        "score_label": f"{score}%" if score is not None else "dados insuficientes",
        "recommendation": _recommendation(classification.label),
        "reasons": classification.reasons or ["Sem alerta objetivo relevante nos dados disponiveis."],
        "warnings": classification.warnings,
        "data_quality": data["data_quality"],
        "active_period": f"{filters.start_day.isoformat()} - {filters.end_day.isoformat()}",
    }


def metrics_payload(db: Session, user_id: int, filters: DashboardFilters) -> list[dict[str, object]]:
    recovery = reports.latest_recovery(db, user_id, filters.day)
    sleep = reports.latest_sleep(db, user_id, filters.day)
    previous_recovery = reports.latest_recovery(db, user_id, filters.day - timedelta(days=1))
    previous_sleep = reports.latest_sleep(db, user_id, filters.day - timedelta(days=1))
    activities = _activities(db, user_id, filters)
    distance = sum(activity.distance_meters or 0 for activity in activities)
    load_7d = _activity_duration(db, user_id, filters.day - timedelta(days=6), filters.day, filters)
    avg28 = _metric_averages(db, user_id, filters.day)
    source_label = _source_label(filters.source)
    updated_at = _latest_update(db, user_id)
    return [
        _metric("recovery", "Recovery", recovery.recovery_score if recovery else None, "%", "WHOOP", updated_at, _delta(recovery, previous_recovery, "recovery_score"), _delta_average(recovery.recovery_score if recovery else None, avg28["recovery"])),
        _metric("hrv", "HRV", recovery.hrv_ms if recovery else None, "ms", "WHOOP", updated_at, _delta(recovery, previous_recovery, "hrv_ms"), _delta_average(recovery.hrv_ms if recovery else None, avg28["hrv"])),
        _metric("resting_hr", "FC repouso", recovery.resting_hr if recovery else None, "bpm", "WHOOP", updated_at, _delta(recovery, previous_recovery, "resting_hr"), _delta_average(recovery.resting_hr if recovery else None, avg28["resting_hr"])),
        _metric("sleep", "Sono", sleep.sleep_duration_seconds if sleep else None, "s", "WHOOP", updated_at, _delta(sleep, previous_sleep, "sleep_duration_seconds"), _delta_average(sleep.sleep_duration_seconds if sleep else None, avg28["sleep_seconds"]), formatter="seconds"),
        _metric("strain", "Strain", recovery.daily_strain if recovery else None, "", "WHOOP", updated_at, _delta(recovery, previous_recovery, "daily_strain"), _delta_average(recovery.daily_strain if recovery else None, avg28["strain"])),
        _metric("load", "Carga 7d", load_7d, "s", source_label, updated_at, None, _delta_average(load_7d, avg28["load_7d_seconds"]), formatter="seconds"),
        _metric("activities", "Atividades", len(activities), "", source_label, updated_at, None, None),
        _metric("distance", "Distancia", distance / 1000 if distance else None, "km", source_label, updated_at, None, _delta_average(distance / 1000 if distance else None, avg28["distance_km"])),
    ]


def trend_payload(db: Session, user_id: int, filters: DashboardFilters) -> dict[str, object]:
    days = _date_range(filters.start_day, filters.end_day)
    recoveries = _recoveries_by_day(db, user_id, filters.start_day, filters.end_day)
    sleeps = _sleeps_by_day(db, user_id, filters.start_day, filters.end_day)
    activities = _activities(db, user_id, filters)
    activity_by_day: dict[date, list[Activity]] = {}
    for activity in activities:
        activity_by_day.setdefault(to_local(activity.started_at).date(), []).append(activity)
    rows: list[dict[str, object]] = []
    for current in days:
        recovery = recoveries.get(current)
        sleep = sleeps.get(current)
        day_activities = activity_by_day.get(current, [])
        rows.append(
            {
                "day": current.isoformat(),
                "recovery": _round(recovery.recovery_score if recovery else None),
                "hrv": _round(recovery.hrv_ms if recovery else None),
                "resting_hr": _round(recovery.resting_hr if recovery else None),
                "sleep_hours": _round((sleep.sleep_duration_seconds or 0) / 3600 if sleep and sleep.sleep_duration_seconds else None),
                "strain": _round(recovery.daily_strain if recovery else None),
                "activity_minutes": round(sum(item.total_duration_seconds or 0 for item in day_activities) / 60),
                "distance_km": _round(sum(item.distance_meters or 0 for item in day_activities) / 1000),
            }
        )
    return {
        "rows": rows,
        "moving_7": _moving_average(rows, 7),
        "moving_28": _moving_average(rows, 28),
        "comparison": _period_comparison(rows),
    }


def activities_payload(db: Session, user_id: int, filters: DashboardFilters) -> dict[str, object]:
    items = _activities(db, user_id, filters)
    if filters.search:
        term = filters.search.lower()
        items = [
            activity
            for activity in items
            if term in (activity.activity_type or "").lower()
            or term in (activity.notes or "").lower()
            or term in (activity.external_id or "").lower()
        ]
    total = len(items)
    start = (filters.page - 1) * filters.page_size
    page_items = items[start : start + filters.page_size]
    return {
        "total": total,
        "page": filters.page,
        "page_size": filters.page_size,
        "rows": [_activity_row(db, item) for item in page_items],
    }


def timeline_payload(db: Session, user_id: int, filters: DashboardFilters) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for sleep in _sleeps(db, user_id, filters.start_day, filters.end_day):
        rows.append(
            {
                "id": f"sleep-{sleep.id}",
                "kind": "Sono",
                "source": _source_name(db, sleep.data_source_id),
                "when": _iso(sleep.started_at) or sleep.day.isoformat(),
                "title": f"Sono {seconds_to_human(sleep.sleep_duration_seconds)}",
                "detail": f"Eficiencia {sleep.efficiency_percent or 'dados insuficientes'}%",
            }
        )
    for recovery in _recoveries(db, user_id, filters.start_day, filters.end_day):
        rows.append(
            {
                "id": f"recovery-{recovery.id}",
                "kind": "Recovery",
                "source": _source_name(db, recovery.data_source_id),
                "when": recovery.day.isoformat(),
                "title": f"Recovery {_value_or_missing(recovery.recovery_score)}%",
                "detail": f"HRV {_value_or_missing(recovery.hrv_ms)} ms, repouso {_value_or_missing(recovery.resting_hr)} bpm",
            }
        )
    for activity in _activities(db, user_id, filters)[:50]:
        rows.append(
            {
                "id": f"activity-{activity.id}",
                "kind": "Atividade",
                "source": _source_name(db, activity.data_source_id),
                "when": _iso(activity.started_at),
                "title": activity.activity_type.title(),
                "detail": f"{seconds_to_human(activity.total_duration_seconds)} - {_distance(activity.distance_meters)}",
                "href": f"/atividades/{activity.id}",
            }
        )
    for summary in db.scalars(
        select(DailySummary)
        .where(DailySummary.user_id == user_id, DailySummary.day >= filters.start_day, DailySummary.day <= filters.end_day)
        .order_by(DailySummary.day.desc())
    ):
        rows.append(
            {
                "id": f"analysis-{summary.id}",
                "kind": "Analise IA",
                "source": "sistema",
                "when": _iso(summary.created_at),
                "title": _classification_label(summary.classification),
                "detail": f"Gerada em {to_local(summary.created_at).strftime('%d/%m %H:%M')}",
            }
        )
    return sorted(rows, key=lambda item: str(item["when"]), reverse=True)[:80]


def calendar_payload(db: Session, user_id: int, selected_day: date) -> dict[str, object]:
    first = selected_day.replace(day=1)
    next_month = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    last = next_month - timedelta(days=1)
    recoveries = _recoveries_by_day(db, user_id, first, last)
    sleeps = _sleeps_by_day(db, user_id, first, last)
    activity_days = {
        to_local(activity.started_at).date()
        for activity in _activities(
            db,
            user_id,
            DashboardFilters(selected_day, first, last, "mes"),
        )
    }
    return {
        "month": first.strftime("%Y-%m"),
        "selected": selected_day.isoformat(),
        "days": [
            {
                "date": current.isoformat(),
                "recovery": _round(recoveries[current].recovery_score) if current in recoveries else None,
                "has_activity": current in activity_days,
                "sleep_hours": _round((sleeps[current].sleep_duration_seconds or 0) / 3600) if current in sleeps and sleeps[current].sleep_duration_seconds else None,
                "quality": _day_quality(recoveries.get(current), sleeps.get(current), current in activity_days),
            }
            for current in _date_range(first, last)
        ],
    }


def data_quality_payload(db: Session, user_id: int, filters: DashboardFilters) -> dict[str, object]:
    sources = []
    for source in db.scalars(select(DataSource).order_by(DataSource.name)).all():
        state = db.scalar(select(IntegrationState).where(IntegrationState.user_id == user_id, IntegrationState.data_source_id == source.id))
        last_log = db.scalar(select(SyncLog).where(SyncLog.data_source_id == source.id).order_by(SyncLog.started_at.desc()).limit(1))
        sources.append(
            {
                "name": source.name,
                "status": state.status if state else "sem estado",
                "last_sync": _iso(state.last_synced_at) if state else None,
                "last_error": state.last_error if state else None,
                "last_log": last_log.status if last_log else None,
            }
        )
    has_recovery = bool(_recoveries(db, user_id, filters.start_day, filters.end_day))
    has_sleep = bool(_sleeps(db, user_id, filters.start_day, filters.end_day))
    has_activity = bool(_activities(db, user_id, filters))
    missing = [
        name
        for name, present in [
            ("recovery", has_recovery),
            ("sono", has_sleep),
            ("atividade", has_activity),
        ]
        if not present
    ]
    level = "completa" if not missing else "parcial" if len(missing) < 3 else "insuficiente"
    return {"level": level, "missing": missing, "sources": sources}


def analysis_payload(db: Session, user_id: int, day: date) -> dict[str, object] | None:
    summary = db.scalar(
        select(DailySummary)
        .where(DailySummary.user_id == user_id, DailySummary.day <= day)
        .order_by(DailySummary.day.desc(), DailySummary.created_at.desc())
        .limit(1)
    )
    if not summary:
        return None
    return {
        "day": summary.day.isoformat(),
        "created_at": _iso(summary.created_at),
        "classification": _classification_label(summary.classification),
        "data_quality": summary.data_quality,
        "sections": _markdown_sections(summary.summary_markdown),
    }


def activities_csv(db: Session, user_id: int, filters: DashboardFilters) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["data", "horario", "nome", "modalidade", "duracao", "distancia_km", "fc_media", "strain", "fonte", "deduplicacao"])
    for row in activities_payload(db, user_id, filters)["rows"]:
        writer.writerow([
            row["date"],
            row["time"],
            row["name"],
            row["type"],
            row["duration"],
            row["distance_km"],
            row["avg_hr"],
            row["strain"],
            row["source"],
            row["duplicate_status"],
        ])
    return output.getvalue()


def _activities(db: Session, user_id: int, filters: DashboardFilters) -> list[Activity]:
    query = (
        select(Activity)
        .join(DataSource, Activity.data_source_id == DataSource.id)
        .where(
            Activity.user_id == user_id,
            Activity.primary_activity_id.is_(None),
            DataSource.name != "demo",
            func.date(Activity.started_at) >= filters.start_day.isoformat(),
            func.date(Activity.started_at) <= filters.end_day.isoformat(),
        )
        .order_by(Activity.started_at.desc())
    )
    source_names = SOURCE_FILTERS.get(filters.source)
    if source_names:
        query = query.where(DataSource.name.in_(source_names))
    elif filters.source == "all":
        query = query.where(DataSource.name.in_(DEFAULT_ACTIVITY_SOURCES))
    if filters.activity_type != "all":
        allowed = ACTIVITY_TYPES.get(filters.activity_type)
        if allowed:
            query = query.where(func.lower(Activity.activity_type).in_(allowed))
        elif filters.activity_type == "outras":
            known = set().union(*ACTIVITY_TYPES.values())
            query = query.where(~func.lower(Activity.activity_type).in_(known))
    items = list(db.scalars(query))
    status_days = _status_days(db, user_id, filters)
    if status_days is not None:
        items = [activity for activity in items if to_local(activity.started_at).date() in status_days]
    if filters.status == "dias_com_treino":
        return items
    if filters.status == "dias_sem_treino":
        return []
    return items


def _activity_duration(db: Session, user_id: int, start_day: date, end_day: date, filters: DashboardFilters) -> int:
    duration_filters = DashboardFilters(filters.day, start_day, end_day, filters.period_label, filters.source, filters.activity_type, "all")
    return sum(activity.total_duration_seconds or 0 for activity in _activities(db, user_id, duration_filters))


def _status_days(db: Session, user_id: int, filters: DashboardFilters) -> set[date] | None:
    if filters.status not in {"recuperacao_baixa", "moderada", "boa"}:
        return None
    days: set[date] = set()
    for recovery in _recoveries(db, user_id, filters.start_day, filters.end_day):
        score = recovery.recovery_score
        if score is None:
            continue
        if filters.status == "recuperacao_baixa" and score < 35:
            days.add(recovery.day)
        elif filters.status == "moderada" and 35 <= score < 70:
            days.add(recovery.day)
        elif filters.status == "boa" and score >= 70:
            days.add(recovery.day)
    return days


def _recoveries(db: Session, user_id: int, start_day: date, end_day: date) -> list[DailyRecovery]:
    return list(
        db.scalars(
            select(DailyRecovery)
            .where(DailyRecovery.user_id == user_id, DailyRecovery.day >= start_day, DailyRecovery.day <= end_day)
            .order_by(DailyRecovery.day.desc())
        )
    )


def _sleeps(db: Session, user_id: int, start_day: date, end_day: date) -> list[Sleep]:
    return list(
        db.scalars(
            select(Sleep)
            .where(Sleep.user_id == user_id, Sleep.day >= start_day, Sleep.day <= end_day)
            .order_by(Sleep.day.desc())
        )
    )


def _recoveries_by_day(db: Session, user_id: int, start_day: date, end_day: date) -> dict[date, DailyRecovery]:
    return {item.day: item for item in _recoveries(db, user_id, start_day, end_day)}


def _sleeps_by_day(db: Session, user_id: int, start_day: date, end_day: date) -> dict[date, Sleep]:
    return {item.day: item for item in _sleeps(db, user_id, start_day, end_day)}


def _activity_row(db: Session, activity: Activity) -> dict[str, object]:
    source = _source_name(db, activity.data_source_id)
    related = db.scalar(select(func.count()).select_from(ActivitySourceLink).where(ActivitySourceLink.activity_id == activity.id)) or 0
    return {
        "id": activity.id,
        "date": to_local(activity.started_at).strftime("%d/%m/%Y"),
        "time": to_local(activity.started_at).strftime("%H:%M"),
        "name": _activity_name(activity, source),
        "type": activity.activity_type.title(),
        "duration": seconds_to_human(activity.total_duration_seconds),
        "duration_seconds": activity.total_duration_seconds or 0,
        "distance_km": _round((activity.distance_meters or 0) / 1000),
        "avg_hr": _round(activity.avg_hr),
        "strain": _round(activity.strain),
        "source": source,
        "duplicate_status": _duplicate_text(activity, related),
        "href": f"/atividades/{activity.id}",
        "external_id": _masked(activity.external_id),
    }


def _activity_name(activity: Activity, source: str) -> str:
    type_label = _activity_type_label(activity.activity_type)
    distance = _distance(activity.distance_meters)
    if distance != "sem distancia":
        return f"{type_label} - {distance}"
    return f"{type_label} - {_source_label(source) or source}"


def _activity_type_label(activity_type: str) -> str:
    normalized = (activity_type or "").lower()
    if normalized in ACTIVITY_TYPES["corrida"]:
        return "Corrida"
    if normalized in ACTIVITY_TYPES["caminhada"]:
        return "Caminhada"
    if normalized in ACTIVITY_TYPES["ciclismo"]:
        return "Ciclismo"
    if normalized in ACTIVITY_TYPES["musculacao"]:
        return "Musculacao"
    if normalized in ACTIVITY_TYPES["natacao"]:
        return "Natacao"
    return (activity_type or "Atividade").title()


def _metric(key, label, value, unit, source, updated_at, delta_previous, delta_average, formatter: str = "number") -> dict[str, object]:
    display = _format_value(value, unit, formatter)
    return {
        "key": key,
        "label": label,
        "value": _round(value),
        "display": display,
        "unit": unit,
        "source": source,
        "updated_at": updated_at,
        "delta_previous": _delta_payload(delta_previous),
        "delta_28d": _delta_payload(delta_average),
        "trend": _trend(delta_previous),
        "status": "dados insuficientes" if value is None else "ok",
    }


def _metric_averages(db: Session, user_id: int, day: date) -> dict[str, float | None]:
    start = day - timedelta(days=27)
    recoveries = _recoveries(db, user_id, start, day)
    sleeps = _sleeps(db, user_id, start, day)
    filters = DashboardFilters(day, start, day, "28 dias")
    activities = _activities(db, user_id, filters)
    return {
        "recovery": _avg([item.recovery_score for item in recoveries]),
        "hrv": _avg([item.hrv_ms for item in recoveries]),
        "resting_hr": _avg([item.resting_hr for item in recoveries]),
        "strain": _avg([item.daily_strain for item in recoveries]),
        "sleep_seconds": _avg([item.sleep_duration_seconds for item in sleeps]),
        "load_7d_seconds": _avg([sum(activity.total_duration_seconds or 0 for activity in activities)]),
        "distance_km": _avg([sum(activity.distance_meters or 0 for activity in activities) / 1000]),
    }


def _latest_update(db: Session, user_id: int) -> str | None:
    state = db.scalar(select(IntegrationState).where(IntegrationState.user_id == user_id).order_by(IntegrationState.updated_at.desc()).limit(1))
    return _iso(state.updated_at) if state else None


def _delta(current, previous, attr: str) -> float | None:
    if not current or not previous:
        return None
    current_value = getattr(current, attr)
    previous_value = getattr(previous, attr)
    if current_value is None or previous_value is None:
        return None
    return float(current_value) - float(previous_value)


def _delta_average(value, average) -> float | None:
    if value is None or average is None:
        return None
    return float(value) - float(average)


def _delta_payload(value: float | None) -> dict[str, object]:
    if value is None:
        return {"display": "dados insuficientes", "value": None}
    return {"display": f"{value:+.1f}", "value": round(value, 1)}


def _trend(delta: float | None) -> str:
    if delta is None:
        return "sem dados"
    if delta > 0:
        return "subiu"
    if delta < 0:
        return "caiu"
    return "estavel"


def _daily_score(recovery: DailyRecovery | None, sleep: Sleep | None) -> int | None:
    if not recovery or recovery.recovery_score is None or not sleep or sleep.sleep_duration_seconds is None:
        return None
    sleep_score = min(100, (sleep.sleep_duration_seconds / (8 * 3600)) * 100)
    return round((float(recovery.recovery_score) * 0.7) + (sleep_score * 0.3))


def _classification_label(label: str) -> str:
    return {
        "descanso": "recuperacao baixa",
        "recuperacao": "recuperacao baixa",
        "leve": "moderada",
        "moderado": "moderada",
        "qualidade": "boa",
    }.get(label, label)


def _recommendation(label: str) -> str:
    return {
        "descanso": "Descanso",
        "recuperacao": "Atividade leve ou descanso",
        "leve": "Atividade leve",
        "moderado": "Treino moderado",
        "qualidade": "Treino intenso, se o plano permitir",
    }.get(label, "dados insuficientes")


def _moving_average(rows: list[dict[str, object]], window: int) -> dict[str, list[float | None]]:
    result: dict[str, list[float | None]] = {}
    for key in ["recovery", "hrv", "resting_hr", "sleep_hours", "strain", "activity_minutes", "distance_km"]:
        values = [row[key] for row in rows]
        series: list[float | None] = []
        for index in range(len(values)):
            clean = [float(item) for item in values[max(0, index - window + 1) : index + 1] if item is not None]
            series.append(round(mean(clean), 1) if clean else None)
        result[key] = series
    return result


def _period_comparison(rows: list[dict[str, object]]) -> dict[str, object]:
    midpoint = max(len(rows) // 2, 1)
    previous = rows[:midpoint]
    current = rows[midpoint:]
    return {
        "current_activity_minutes": sum(int(row["activity_minutes"] or 0) for row in current),
        "previous_activity_minutes": sum(int(row["activity_minutes"] or 0) for row in previous),
        "current_distance_km": round(sum(float(row["distance_km"] or 0) for row in current), 1),
        "previous_distance_km": round(sum(float(row["distance_km"] or 0) for row in previous), 1),
    }


def _date_range(start_day: date, end_day: date) -> list[date]:
    return [start_day + timedelta(days=offset) for offset in range((end_day - start_day).days + 1)]


def _filters_payload(filters: DashboardFilters) -> dict[str, object]:
    return {
        "day": filters.day.isoformat(),
        "start_date": filters.start_day.isoformat(),
        "end_date": filters.end_day.isoformat(),
        "period": filters.period_label,
        "source": filters.source,
        "activity_type": filters.activity_type,
        "status": filters.status,
        "search": filters.search,
    }


def _format_value(value, unit: str, formatter: str) -> str:
    if value is None:
        return "dados insuficientes"
    if formatter == "seconds":
        return seconds_to_human(int(value))
    if unit == "km":
        return f"{float(value):.1f} km"
    if unit:
        return f"{float(value):.1f} {unit}" if isinstance(value, float) else f"{value} {unit}"
    return str(value)


def _round(value) -> float | None:
    if value is None:
        return None
    return round(float(value), 1)


def _avg(values: list[float | int | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return round(mean(clean), 1) if clean else None


def _source_name(db: Session, source_id: int | None) -> str:
    if not source_id:
        return "sistema"
    source = db.get(DataSource, source_id)
    return source.name if source else "sistema"


def _source_label(source: str) -> str:
    return {"all": "Strava/Garmin", "strava": "Strava", "whoop": "WHOOP", "garmin_fit": "Garmin/FIT"}.get(source, source)


def _iso(value: datetime | None) -> str | None:
    if not value:
        return None
    return to_local(value).isoformat()


def _value_or_missing(value) -> str:
    return "dados insuficientes" if value is None else str(_round(value))


def _distance(value: float | None) -> str:
    return f"{round(value / 1000, 1)} km" if value else "sem distancia"


def _day_quality(recovery: DailyRecovery | None, sleep: Sleep | None, has_activity: bool) -> str:
    present = sum([recovery is not None, sleep is not None, has_activity])
    return "completa" if present >= 3 else "parcial" if present else "insuficiente"


def _duplicate_text(activity: Activity, related_count: int) -> str:
    if activity.primary_activity_id:
        return "registro relacionado"
    if related_count:
        return f"principal com {related_count} fonte(s)"
    return activity.duplicate_status or "unico"


def _masked(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return value[:2] + "***"
    return f"{value[:4]}...{value[-4:]}"


def _markdown_sections(markdown: str) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    current_title = "Como voce esta hoje"
    current_lines: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("## "):
            if current_lines:
                sections.append({"title": _section_title(current_title), "body": _clean_markdown("\n".join(current_lines))})
                current_lines = []
            current_title = line[3:].strip()
        elif line.startswith("# "):
            continue
        else:
            current_lines.append(line)
    if current_lines:
        sections.append({"title": _section_title(current_title), "body": _clean_markdown("\n".join(current_lines))})
    return [section for section in sections if section["body"].strip()]


def _section_title(title: str) -> str:
    mapping = {
        "Prontidao": "Como voce esta hoje",
        "Carga": "Carga recente",
        "Atividade recente": "Principais sinais",
        "Recomendacao operacional": "Recomendacao",
        "Limites da analise": "Limitacoes dos dados",
        "Evidencias": "Principais sinais",
    }
    return mapping.get(title, title)


def _clean_markdown(text: str) -> str:
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = text.replace("- ", "• ")
    return text.strip()
