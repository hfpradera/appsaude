import json

from app.models import User
from app.services.importers import import_csv, import_json


def test_import_json_activity(tmp_path, db_session):
    user = User(name="Humberto")
    db_session.add(user)
    db_session.flush()
    path = tmp_path / "activities.json"
    path.write_text(json.dumps({"activities": [{"activity_type": "corrida", "started_at": "2026-07-11T10:00:00Z", "duration_seconds": 1800, "distance_meters": 5000}]}), encoding="utf-8")
    assert import_json(db_session, user.id, path) == 1


def test_import_csv_activity(tmp_path, db_session):
    user = User(name="Humberto")
    db_session.add(user)
    db_session.flush()
    path = tmp_path / "activities.csv"
    path.write_text("activity_type,started_at,duration_seconds,distance_meters\nbike,2026-07-11T10:00:00Z,3600,20000\n", encoding="utf-8")
    assert import_csv(db_session, user.id, path) == 1
