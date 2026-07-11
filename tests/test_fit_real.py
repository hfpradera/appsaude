from pathlib import Path

import pytest

from app.models import Activity, ActivityLap, ActivitySample, User
from app.services.importers import import_fit
from app.services.timezone import to_local

FIT_PATH = Path("data/uploads/23558887635_ACTIVITY.fit")


@pytest.mark.skipif(not FIT_PATH.exists(), reason="FIT real nao esta disponivel localmente")
def test_real_running_fit_import_is_complete_and_idempotent(db_session):
    user = User(name="Humberto")
    db_session.add(user)
    db_session.flush()

    assert import_fit(db_session, user.id, FIT_PATH) == 1
    assert import_fit(db_session, user.id, FIT_PATH) == 0

    activity = db_session.query(Activity).one()
    assert activity.activity_type == "running"
    assert activity.distance_meters == pytest.approx(4004.25, abs=25)
    assert activity.total_duration_seconds == pytest.approx(1569, abs=2)
    assert activity.avg_hr == pytest.approx(157, abs=2)
    assert activity.avg_pace_seconds_per_km == pytest.approx(392, abs=3)
    assert to_local(activity.started_at).hour == 9
    assert len(db_session.query(ActivityLap).all()) == 5
    assert len(db_session.query(ActivitySample).all()) >= 1000
    assert "position" not in (activity.notes or "").lower()
    assert "_lat" not in (activity.notes or "").lower()
