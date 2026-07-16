from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DailySummary
from app.services import reports
from app.services.timezone import seconds_to_human


def generate_daily_analysis(db: Session, user_id: int, day: date) -> DailySummary:
    data = reports.dashboard(db, user_id, day)
    text = _local_analysis(data)
    existing = db.scalar(
        select(DailySummary)
        .where(DailySummary.user_id == user_id, DailySummary.day == day)
        .order_by(DailySummary.created_at.desc())
        .limit(1)
    )
    summary = existing or DailySummary(
        user_id=user_id,
        day=day,
        classification=data["classification"].label,
        summary_markdown="",
    )
    summary.classification = data["classification"].label
    summary.summary_markdown = text
    summary.data_quality = str(data["data_quality"])
    db.add(summary)
    db.commit()
    db.refresh(summary)
    return summary


def latest_daily_analysis(db: Session, user_id: int, day: date) -> DailySummary | None:
    return db.scalar(
        select(DailySummary)
        .where(DailySummary.user_id == user_id, DailySummary.day <= day)
        .order_by(DailySummary.day.desc(), DailySummary.created_at.desc())
        .limit(1)
    )


def ai_status() -> dict[str, object]:
    settings = get_settings()
    return {
        "enabled": settings.ai_enabled,
        "provider": settings.ai_provider,
        "mode": "local" if not settings.ai_enabled else settings.ai_provider,
    }


def _local_analysis(data: dict[str, object]) -> str:
    recovery = data["recovery"]
    sleep = data["sleep"]
    classification = data["classification"]
    week = data["weekly_duration"]
    load_7d = data["load_7d_seconds"]
    load_28d = data["load_28d_seconds"]
    recent = data["recent_activities"][0] if data["recent_activities"] else None
    lines = [
        f"# Analise diaria - {data['day']}",
        "",
        f"Leitura executiva: **{classification.label}**.",
        "",
        "## Prontidao",
        _readiness_text(recovery, sleep),
        "",
        "## Carga",
        (
            f"Os ultimos 7 dias somam {seconds_to_human(load_7d)}. "
            f"Os ultimos 28 dias somam {seconds_to_human(load_28d)}. "
            f"Na semana atual, o total e {week}."
        ),
        "",
        "## Atividade recente",
        _recent_text(recent),
        "",
        "## Recomendacao operacional",
        reports.conservative_suggestion(classification),
        "",
        "## Limites da analise",
        (
            "Esta analise usa exclusivamente Recovery, HRV, sono, strain, repouso e "
            "atividades sincronizadas no banco local. Nao e diagnostico medico."
        ),
    ]
    if classification.reasons:
        lines.extend(["", "## Evidencias", *[f"- {reason}" for reason in classification.reasons]])
    return "\n".join(lines)


def _readiness_text(recovery, sleep) -> str:
    parts: list[str] = []
    if recovery:
        parts.append(
            f"Recovery {recovery.recovery_score or '-'}%, HRV {recovery.hrv_ms or '-'} ms, "
            f"FC repouso {recovery.resting_hr or '-'} bpm, strain {recovery.daily_strain or '-'}."
        )
    else:
        parts.append("Sem dado de recovery sincronizado.")
    if sleep:
        parts.append(
            f"Sono {seconds_to_human(sleep.sleep_duration_seconds)}, "
            f"eficiencia {sleep.efficiency_percent or '-'}%."
        )
    else:
        parts.append("Sem dado de sono sincronizado.")
    return " ".join(parts)


def _recent_text(activity) -> str:
    if not activity:
        return "Nenhuma atividade recente sincronizada."
    distance = f"{round((activity.distance_meters or 0) / 1000, 1)} km" if activity.distance_meters else "sem distancia"
    return (
        f"Ultima atividade: {activity.activity_type}, {distance}, "
        f"{seconds_to_human(activity.total_duration_seconds)}."
    )
