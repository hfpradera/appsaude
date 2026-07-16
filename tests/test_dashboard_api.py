from datetime import UTC, date, datetime, timedelta

from app.models import Activity, DailyRecovery, DailySummary, DataSource, Sleep, User
from app.services.dashboard_api import (
    analysis_payload,
    build_filters,
    dashboard_payload,
    data_quality_payload,
)


def _seed_dashboard(db_session):
    user = User(name="Humberto")
    strava = DataSource(name="strava", kind="oauth")
    fit = DataSource(name="garmin_fit", kind="file")
    whoop = DataSource(name="whoop", kind="oauth")
    demo = DataSource(name="demo", kind="demo")
    db_session.add_all([user, strava, fit, whoop, demo])
    db_session.flush()
    day = date(2026, 7, 12)
    db_session.add(
        DailyRecovery(
            user_id=user.id,
            data_source_id=whoop.id,
            day=day,
            recovery_score=28,
            hrv_ms=20.0,
            resting_hr=71,
            daily_strain=9.2,
        )
    )
    db_session.add(
        Sleep(
            user_id=user.id,
            data_source_id=whoop.id,
            day=day,
            sleep_duration_seconds=7 * 3600,
            efficiency_percent=92,
        )
    )
    db_session.add_all(
        [
            Activity(
                user_id=user.id,
                data_source_id=fit.id,
                external_id="fit-run",
                activity_type="running",
                started_at=datetime(2026, 7, 12, 12, 42, tzinfo=UTC),
                total_duration_seconds=1569,
                distance_meters=4004,
                avg_hr=157,
            ),
            Activity(
                user_id=user.id,
                data_source_id=strava.id,
                external_id="strava-walk",
                activity_type="walk",
                started_at=datetime(2026, 7, 11, 13, 8, tzinfo=UTC),
                total_duration_seconds=1136,
                distance_meters=1640,
            ),
            Activity(
                user_id=user.id,
                data_source_id=whoop.id,
                external_id="whoop-workout",
                activity_type="running",
                started_at=datetime(2026, 7, 12, 14, 1, tzinfo=UTC),
                total_duration_seconds=2099,
                strain=10.7,
            ),
            Activity(
                user_id=user.id,
                data_source_id=demo.id,
                external_id="demo-bike",
                activity_type="bike",
                started_at=datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
                total_duration_seconds=4200,
                distance_meters=23000,
            ),
        ]
    )
    db_session.add(
        DailySummary(
            user_id=user.id,
            day=day,
            classification="leve",
            data_quality="parcial",
            summary_markdown="# Analise diaria\n\n## Prontidao\nRecovery **28%**.\n\n## Recomendacao operacional\nTreino leve.",
        )
    )
    db_session.commit()
    return user, day


def test_dashboard_default_filters_exclude_demo_and_whoop_workouts(db_session):
    user, day = _seed_dashboard(db_session)
    filters = build_filters(day=day, period="7")

    payload = dashboard_payload(db_session, user.id, filters)

    activity_rows = payload["activities"]["rows"]
    assert {row["source"] for row in activity_rows} == {"garmin_fit", "strava"}
    assert payload["activities"]["total"] == 2
    assert payload["metrics"][7]["display"] == "5.6 km"


def test_activity_table_uses_human_name_not_fit_audit_notes(db_session):
    user, day = _seed_dashboard(db_session)
    activity = db_session.query(Activity).filter_by(external_id="fit-run").one()
    activity.notes = "Origem: FIT Garmin | Dispositivo: Garmin produto 4024 | Campos presentes: avg_heart_rate, distance"
    db_session.commit()

    payload = dashboard_payload(db_session, user.id, build_filters(day=day, period="7"))

    fit_row = next(row for row in payload["activities"]["rows"] if row["source"] == "garmin_fit")
    assert fit_row["name"] == "Corrida - 4.0 km"
    assert "Origem:" not in fit_row["name"]
    assert "Campos presentes" not in fit_row["name"]


def test_dashboard_source_filter_can_audit_whoop_workouts(db_session):
    user, day = _seed_dashboard(db_session)
    filters = build_filters(day=day, period="7", source="whoop")

    payload = dashboard_payload(db_session, user.id, filters)

    assert payload["activities"]["total"] == 1
    assert payload["activities"]["rows"][0]["source"] == "whoop"
    assert payload["activities"]["rows"][0]["strain"] == 10.7


def test_dashboard_data_quality_reports_missing_metrics(db_session):
    user, day = _seed_dashboard(db_session)
    filters = build_filters(day=day + timedelta(days=10), period="today")

    quality = data_quality_payload(db_session, user.id, filters)

    assert quality["level"] == "insuficiente"
    assert set(quality["missing"]) == {"recovery", "sono", "atividade"}


def test_analysis_payload_is_structured_not_raw_markdown(db_session):
    user, day = _seed_dashboard(db_session)

    analysis = analysis_payload(db_session, user.id, day)

    assert analysis is not None
    assert analysis["sections"][0]["title"] == "Como voce esta hoje"
    assert "**" not in analysis["sections"][0]["body"]
