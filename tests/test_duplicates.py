from datetime import UTC, datetime, timedelta

from app.models import Activity, DataSource, User
from app.services.reconciliation import mark_duplicates_for_review


def test_marks_possible_duplicate_without_deleting(db_session):
    user = User(name="Humberto")
    source = DataSource(name="test")
    db_session.add_all([user, source])
    db_session.flush()
    started = datetime(2026, 7, 11, 10, tzinfo=UTC)
    first = Activity(user_id=user.id, data_source_id=source.id, activity_type="corrida", started_at=started, total_duration_seconds=3600)
    second = Activity(user_id=user.id, data_source_id=source.id, activity_type="corrida", started_at=started + timedelta(minutes=4), total_duration_seconds=3540)
    db_session.add_all([first, second])
    db_session.flush()
    matches = mark_duplicates_for_review(db_session, second)
    assert matches == [first]
    assert first.duplicate_status == "needs_review"
    assert second.duplicate_status == "needs_review"
    assert db_session.query(Activity).count() == 2
