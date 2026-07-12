from dataclasses import dataclass

from app.models import Activity


@dataclass(frozen=True)
class ReconciliationResult:
    decision: str
    confidence: str
    score: float
    candidate_activity_id: int | None
    evidence: dict[str, object]
    reason: str


def reconcile_strava_activity(payload: dict, candidates: list[Activity]) -> ReconciliationResult:
    if not candidates:
        return ReconciliationResult("separate", "low", 0.0, None, {}, "Sem candidata local.")
    candidate = min(candidates, key=lambda item: abs((item.started_at - payload["started_at"]).total_seconds()))
    start = abs((candidate.started_at - payload["started_at"]).total_seconds())
    duration = abs((candidate.total_duration_seconds or 0) - (payload.get("elapsed_time") or 0))
    distance = abs((candidate.distance_meters or 0) - (payload.get("distance") or 0))
    duration_ratio = duration / max(candidate.total_duration_seconds or 1, 1)
    distance_ratio = distance / max(candidate.distance_meters or 1, 1)
    compatible = candidate.activity_type.lower() in str(payload.get("sport_type") or payload.get("type") or "").lower()
    score = sum(
        [
            0.35 if start <= 120 else 0.15 if start <= 600 else 0,
            0.3 if duration_ratio <= 0.05 else 0.15 if duration_ratio <= 0.15 else 0,
            0.3 if distance_ratio <= 0.05 else 0.15 if distance_ratio <= 0.15 else 0,
            0.05 if compatible else 0,
        ]
    )
    decision = "linked" if compatible and score >= 0.85 else "possible_duplicate" if score >= 0.60 else "separate"
    confidence = "high" if decision == "linked" else "medium" if decision == "possible_duplicate" else "low"
    evidence = {
        "start_delta_seconds": start,
        "duration_delta_seconds": duration,
        "duration_delta_ratio": duration_ratio,
        "distance_delta_meters": distance,
        "distance_delta_ratio": distance_ratio,
        "type_compatible": compatible,
        "score": score,
        "decision": decision,
    }
    return ReconciliationResult(decision, confidence, score, candidate.id, evidence, "Sinais de horario, duracao, distancia e tipo.")
