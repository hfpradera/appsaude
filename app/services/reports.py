import json
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean, pstdev

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Activity, DailyRecovery, Sleep, SubjectiveCheckin
from app.services.timezone import seconds_to_human


@dataclass(frozen=True)
class DayClassification:
    label: str
    reasons: list[str]
    warnings: list[str]
    data_quality: str


def classify_day(
    recovery: DailyRecovery | None,
    sleep: Sleep | None,
    checkin: SubjectiveCheckin | None,
    load_7d_seconds: int,
    load_28d_seconds: int,
) -> DayClassification:
    reasons: list[str] = []
    warnings: list[str] = []
    missing = 0

    if recovery is None:
        missing += 1
        reasons.append("Sem dado de recuperacao.")
    if sleep is None:
        missing += 1
        reasons.append("Sem dado de sono.")
    if checkin is None:
        missing += 1
        reasons.append("Sem check-in subjetivo.")

    red_flags = (checkin.red_flags or "").lower() if checkin else ""
    if any(flag in red_flags for flag in ["peito", "desmaio", "falta de ar", "palpit", "dor forte"]):
        warnings.append("Sinal de alerta informado. Evite treino intenso e procure avaliacao profissional adequada.")
        return DayClassification("descanso", reasons + ["Regra de seguranca acionada."], warnings, _quality(missing))

    low_recovery = recovery and recovery.recovery_score is not None and recovery.recovery_score < 35
    low_sleep = sleep and sleep.sleep_duration_seconds is not None and sleep.sleep_duration_seconds < 6 * 3600
    high_soreness = checkin and checkin.muscle_soreness is not None and checkin.muscle_soreness >= 7
    low_energy = checkin and checkin.energy is not None and checkin.energy <= 3
    load_ratio = load_7d_seconds / (load_28d_seconds / 4) if load_28d_seconds else 0

    if low_recovery:
        reasons.append("Recovery abaixo de 35%.")
    if low_sleep:
        reasons.append("Sono abaixo de 6h.")
    if high_soreness:
        reasons.append("Dor muscular alta.")
    if low_energy:
        reasons.append("Energia baixa.")
    if load_ratio > 1.35:
        reasons.append("Carga recente acima da media das ultimas 4 semanas.")

    risk_points = sum(bool(x) for x in [low_recovery, low_sleep, high_soreness, low_energy]) + (1 if load_ratio > 1.35 else 0)
    if risk_points >= 3:
        return DayClassification("recuperacao", reasons, warnings, _quality(missing))
    if risk_points == 2:
        return DayClassification("leve", reasons, warnings, _quality(missing))
    if missing >= 2:
        return DayClassification("leve", reasons + ["Dados insuficientes para sugerir intensidade."], warnings, _quality(missing))
    if recovery and recovery.recovery_score and recovery.recovery_score >= 70 and not low_sleep and load_ratio <= 1.20:
        return DayClassification("qualidade", reasons + ["Boa recuperacao e carga controlada."], warnings, _quality(missing))
    return DayClassification("moderado", reasons or ["Indicadores principais sem alerta relevante."], warnings, _quality(missing))


def dashboard(db: Session, user_id: int, day: date) -> dict[str, object]:
    recovery = latest_recovery(db, user_id, day)
    sleep = latest_sleep(db, user_id, day)
    checkin = latest_checkin(db, user_id, day)
    recent_activity = db.scalar(
        select(Activity)
        .where(Activity.user_id == user_id)
        .order_by(Activity.started_at.desc())
        .limit(1)
    )
    load_7d = activity_load_seconds(db, user_id, day - timedelta(days=6), day)
    load_28d = activity_load_seconds(db, user_id, day - timedelta(days=27), day)
    classification = classify_day(recovery, sleep, checkin, load_7d, load_28d)
    week_distance = activity_distance(db, user_id, day - timedelta(days=6), day)
    week_duration = activity_load_seconds(db, user_id, day - timedelta(days=6), day)
    previous_4w_avg = activity_load_seconds(db, user_id, day - timedelta(days=34), day - timedelta(days=7)) / 4
    return {
        "day": day,
        "recovery": recovery,
        "sleep": sleep,
        "checkin": checkin,
        "recent_activity": recent_activity,
        "load_7d_seconds": load_7d,
        "load_28d_seconds": load_28d,
        "weekly_distance_km": round(week_distance / 1000, 1),
        "weekly_duration": seconds_to_human(week_duration),
        "four_week_avg": seconds_to_human(previous_4w_avg),
        "classification": classification,
        "data_quality": classification.data_quality,
    }


def daily_report_markdown(db: Session, user_id: int, day: date) -> str:
    data = dashboard(db, user_id, day)
    classification: DayClassification = data["classification"]  # type: ignore[assignment]
    recovery = data["recovery"]
    sleep = data["sleep"]
    recent = data["recent_activity"]
    lines = [
        f"# Relatorio diario - {day.isoformat()}",
        "",
        f"Classificacao conservadora: **{classification.label}**.",
        "",
        "## Condicao geral",
        "; ".join(classification.reasons),
        "",
        "## Recuperacao",
        _recovery_text(recovery),
        "",
        "## Sono",
        _sleep_text(sleep),
        "",
        "## Treino anterior",
        _activity_text(recent),
        "",
        "## Carga recente",
        f"Ultimos 7 dias: {seconds_to_human(data['load_7d_seconds'])}. Ultimos 28 dias: {seconds_to_human(data['load_28d_seconds'])}.",
        "",
        "## Sugestao conservadora",
        conservative_suggestion(classification),
        "",
        "## Dados ausentes ou inconsistentes",
        f"Qualidade dos dados: {classification.data_quality}.",
    ]
    if classification.warnings:
        lines.extend(["", "## Alertas", *classification.warnings])
    return "\n".join(lines)


def weekly_report(db: Session, user_id: int, end_day: date) -> dict[str, object]:
    start_day = end_day - timedelta(days=6)
    previous_start = start_day - timedelta(days=7)
    previous_end = start_day - timedelta(days=1)
    activities = activities_between(db, user_id, start_day, end_day)
    previous_duration = activity_load_seconds(db, user_id, previous_start, previous_end)
    duration = sum(a.total_duration_seconds or 0 for a in activities)
    distance = sum(a.distance_meters or 0 for a in activities)
    loads = [a.total_duration_seconds or 0 for a in activities]
    monotony = round(mean(loads) / pstdev(loads), 2) if len(loads) > 1 and pstdev(loads) else 0
    recoveries = db.scalars(
        select(DailyRecovery).where(
            DailyRecovery.user_id == user_id,
            DailyRecovery.day >= start_day,
            DailyRecovery.day <= end_day,
        )
    ).all()
    sleeps = db.scalars(
        select(Sleep).where(Sleep.user_id == user_id, Sleep.day >= start_day, Sleep.day <= end_day)
    ).all()
    return {
        "start_day": start_day,
        "end_day": end_day,
        "workouts": len(activities),
        "distance_km": round(distance / 1000, 1),
        "duration": seconds_to_human(duration),
        "progression_percent": round(((duration - previous_duration) / previous_duration) * 100, 1)
        if previous_duration
        else None,
        "monotony": monotony,
        "avg_recovery": _avg([r.recovery_score for r in recoveries]),
        "avg_hrv": _avg([r.hrv_ms for r in recoveries]),
        "avg_resting_hr": _avg([r.resting_hr for r in recoveries]),
        "avg_sleep": seconds_to_human(_avg([s.sleep_duration_seconds for s in sleeps])),
        "alerts": _weekly_alerts(duration, previous_duration, monotony),
        "recommendation": "Mantenha progressao gradual e priorize sono. Correlacoes de habitos sao exploratorias, nao causais.",
    }


def export_json(db: Session, user_id: int, day: date) -> str:
    payload = {
        "dashboard": _json_safe(dashboard(db, user_id, day)),
        "weekly_report": _json_safe(weekly_report(db, user_id, day)),
        "daily_report_markdown": daily_report_markdown(db, user_id, day),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def latest_recovery(db: Session, user_id: int, day: date) -> DailyRecovery | None:
    return db.scalar(
        select(DailyRecovery)
        .where(DailyRecovery.user_id == user_id, DailyRecovery.day <= day)
        .order_by(DailyRecovery.day.desc())
        .limit(1)
    )


def latest_sleep(db: Session, user_id: int, day: date) -> Sleep | None:
    return db.scalar(
        select(Sleep)
        .where(Sleep.user_id == user_id, Sleep.day <= day)
        .order_by(Sleep.day.desc())
        .limit(1)
    )


def latest_checkin(db: Session, user_id: int, day: date) -> SubjectiveCheckin | None:
    return db.scalar(select(SubjectiveCheckin).where(SubjectiveCheckin.user_id == user_id, SubjectiveCheckin.day == day))


def activity_load_seconds(db: Session, user_id: int, start_day: date, end_day: date) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(Activity.total_duration_seconds), 0)).where(
                Activity.user_id == user_id,
                func.date(Activity.started_at) >= start_day.isoformat(),
                func.date(Activity.started_at) <= end_day.isoformat(),
            )
        )
        or 0
    )


def activity_distance(db: Session, user_id: int, start_day: date, end_day: date) -> float:
    return float(
        db.scalar(
            select(func.coalesce(func.sum(Activity.distance_meters), 0)).where(
                Activity.user_id == user_id,
                func.date(Activity.started_at) >= start_day.isoformat(),
                func.date(Activity.started_at) <= end_day.isoformat(),
            )
        )
        or 0
    )


def activities_between(db: Session, user_id: int, start_day: date, end_day: date) -> list[Activity]:
    return list(
        db.scalars(
            select(Activity)
            .where(
                Activity.user_id == user_id,
                func.date(Activity.started_at) >= start_day.isoformat(),
                func.date(Activity.started_at) <= end_day.isoformat(),
            )
            .order_by(Activity.started_at.desc())
        )
    )


def conservative_suggestion(classification: DayClassification) -> str:
    suggestions = {
        "descanso": "Evite intensidade. Considere descanso e avaliacao profissional se houver sinais de alerta.",
        "recuperacao": "Escolha recuperacao ativa leve ou descanso. Nao force intensidade.",
        "leve": "Treino leve e curto, mantendo conversa confortavel.",
        "moderado": "Treino moderado pode ser considerado se nao houver dor ou piora durante a atividade.",
        "qualidade": "Sessao de qualidade pode ser considerada, ainda respeitando aquecimento, sensacoes e plano do treinador.",
    }
    return suggestions[classification.label]


def _quality(missing_count: int) -> str:
    if missing_count == 0:
        return "boa"
    if missing_count == 1:
        return "parcial"
    return "limitada"


def _avg(values: list[float | int | None]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    return round(mean(clean), 1) if clean else None


def _weekly_alerts(duration: int, previous_duration: int, monotony: float) -> list[str]:
    alerts: list[str] = []
    if previous_duration and duration > previous_duration * 1.35:
        alerts.append("Progressao semanal acima de 35%.")
    if monotony >= 2:
        alerts.append("Monotonia de treino elevada.")
    return alerts or ["Sem alerta forte com os dados disponiveis."]


def _recovery_text(recovery: DailyRecovery | None) -> str:
    if not recovery:
        return "Sem dado de recuperacao."
    return f"Recovery {recovery.recovery_score or '-'}%, HRV {recovery.hrv_ms or '-'} ms, repouso {recovery.resting_hr or '-'} bpm."


def _sleep_text(sleep: Sleep | None) -> str:
    if not sleep:
        return "Sem dado de sono."
    return f"Duracao {seconds_to_human(sleep.sleep_duration_seconds)}, eficiencia {sleep.efficiency_percent or '-'}%."


def _activity_text(activity: Activity | None) -> str:
    if not activity:
        return "Sem treino registrado."
    distance = f"{round((activity.distance_meters or 0) / 1000, 1)} km" if activity.distance_meters else "sem distancia"
    return f"{activity.activity_type.title()} com {distance} e duracao {seconds_to_human(activity.total_duration_seconds)}."


def _json_safe(value: object) -> object:
    if hasattr(value, "__dict__"):
        return {k: _json_safe(v) for k, v in value.__dict__.items() if not k.startswith("_")}
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value
