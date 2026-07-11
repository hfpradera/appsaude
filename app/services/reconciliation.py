from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Activity, ActivityRelationship


def detect_possible_duplicates(
    db: Session,
    activity: Activity,
    start_tolerance_minutes: int = 10,
    duration_tolerance_ratio: float = 0.10,
) -> list[Activity]:
    lower = activity.started_at - timedelta(minutes=start_tolerance_minutes)
    upper = activity.started_at + timedelta(minutes=start_tolerance_minutes)
    candidates = db.scalars(
        select(Activity).where(
            Activity.id != activity.id,
            Activity.user_id == activity.user_id,
            Activity.activity_type == activity.activity_type,
            Activity.started_at >= lower,
            Activity.started_at <= upper,
        )
    ).all()
    matches: list[Activity] = []
    for candidate in candidates:
        if _duration_close(activity.total_duration_seconds, candidate.total_duration_seconds, duration_tolerance_ratio):
            matches.append(candidate)
    return matches


def mark_duplicates_for_review(db: Session, activity: Activity) -> list[Activity]:
    matches = detect_possible_duplicates(db, activity)
    for match in matches:
        activity.duplicate_status = "needs_review"
        match.duplicate_status = "needs_review"
        reason = "Mesmo tipo, inicio proximo e duracao semelhante. Revisao manual necessaria."
        relation = ActivityRelationship(
            activity_id=activity.id,
            related_activity_id=match.id,
            relationship_type="possible_duplicate",
            decision_reason=reason,
        )
        db.add(relation)
        activity.duplicate_decision = reason
        match.duplicate_decision = reason
    return matches


def _duration_close(a: int | None, b: int | None, tolerance_ratio: float) -> bool:
    if not a or not b:
        return True
    return abs(a - b) <= max(60, min(a, b) * tolerance_ratio)
