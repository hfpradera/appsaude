from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    Activity,
    ActivitySample,
    ActivitySourceLink,
    DataSource,
    IntegrationState,
    OAuthCredential,
    SyncLog,
    User,
)
from app.services.reconcile_new import reconcile_strava_activity
from app.services.strava import (
    StravaAuthenticationError,
    StravaError,
    StravaRateLimitError,
    encrypt_token,
)
from app.services.sync import sync_strava


class FakeClient:
    def __init__(self, pages=None, refresh_response=None, error=None, streams=None, stream_error=None):
        self.pages = pages or []
        self.refresh_response = refresh_response
        self.error = error
        self.streams = streams or stream_payload()
        self.stream_error = stream_error
        self.refreshed = False
        self.stream_calls = []

    def refresh(self, _token):
        self.refreshed = True
        return self.refresh_response

    def activities(self, _token, after=None):
        if self.error:
            raise self.error
        self.after = after
        return [item for page in self.pages for item in page]

    def activity_streams(self, _token, activity_id):
        if self.stream_error:
            raise self.stream_error
        self.stream_calls.append(activity_id)
        return self.streams


@pytest.fixture()
def connected(monkeypatch, db_session):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "mGFWnXyGmDvQHhpbJ4slZOTNupC6lDltJ-NvCRHnF74=")
    get_settings.cache_clear()
    user = User(name="Humberto")
    source = DataSource(name="strava", kind="oauth")
    db_session.add_all([user, source])
    db_session.flush()
    credential = OAuthCredential(
        user_id=user.id,
        data_source_id=source.id,
        encrypted_access_token=encrypt_token("access"),
        encrypted_refresh_token=encrypt_token("refresh"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(credential)
    db_session.commit()
    yield user, source, credential
    get_settings.cache_clear()


def payload(activity_id=99, **extra):
    value = {
        "id": activity_id,
        "name": "Corrida referencia",
        "type": "Run",
        "sport_type": "Run",
        "start_date": "2026-07-11T12:42:05Z",
        "start_date_local": "2026-07-11T09:42:05Z",
        "timezone": "(GMT-03:00) America/Sao_Paulo",
        "elapsed_time": 1569,
        "moving_time": 1569,
        "distance": 4004.25,
        "average_speed": 2.55,
        "max_speed": 4.2,
        "average_heartrate": 157,
        "max_heartrate": 176,
        "total_elevation_gain": 17,
    }
    value.update(extra)
    return value


def stream_payload():
    return {
        "time": {"data": [0, 1, 2]},
        "distance": {"data": [0.0, 2.5, 5.1]},
        "heartrate": {"data": [150, 151, 152]},
        "velocity_smooth": {"data": [2.5, 2.6, 2.7]},
        "cadence": {"data": [80, 81, 82]},
        "watts": {"data": [200, 201, 202]},
        "altitude": {"data": [700.0, 700.2, 700.4]},
        "temp": {"data": [22, 22, 23]},
        "moving": {"data": [True, True, True]},
        "grade_smooth": {"data": [0.0, 0.1, 0.0]},
    }


def create_fit_activity(db_session, user, samples=1570, **extra):
    source = DataSource(name=extra.pop("source_name", "garmin"), kind="fit")
    activity = Activity(
        user_id=user.id,
        data_source_id=None,
        external_id="fit-ref",
        activity_type="run",
        started_at=datetime(2026, 7, 11, 12, 42, 5, tzinfo=UTC),
        total_duration_seconds=1569,
        moving_time_seconds=1569,
        distance_meters=4004.25,
        avg_hr=157,
        elevation_gain_meters=17,
        **extra,
    )
    db_session.add(source)
    db_session.flush()
    activity.data_source_id = source.id
    db_session.add(activity)
    db_session.flush()
    for index in range(samples):
        db_session.add(
            ActivitySample(
                activity_id=activity.id,
                recorded_at=activity.started_at + timedelta(seconds=index),
                heart_rate=140 + (index % 40),
            )
        )
    db_session.commit()
    return activity


def links(db_session):
    return db_session.scalars(select(ActivitySourceLink)).all()


def sample_count(db_session, activity):
    return db_session.query(ActivitySample).filter(ActivitySample.activity_id == activity.id).count()


def test_sync_imports_once_and_preserves_missing_fields(db_session, connected):
    user, source, _ = connected
    client = FakeClient(pages=[[payload(1), payload(2, average_heartrate=None)]])
    assert sync_strava(db_session, user.id, client) == {
        "fetched": 2,
        "created": 2,
        "linked": 0,
        "possible_duplicates": 0,
        "skipped": 0,
        "errors": 0,
    }
    assert sync_strava(db_session, user.id, client)["skipped"] == 2
    assert db_session.query(Activity).filter(Activity.data_source_id == source.id).count() == 2


def test_sync_refreshes_expiring_token(db_session, connected):
    user, _, credential = connected
    credential.expires_at = datetime.now(UTC)
    client = FakeClient(
        pages=[[]],
        refresh_response={
            "access_token": "new",
            "refresh_token": "new-refresh",
            "expires_at": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        },
    )
    sync_strava(db_session, user.id, client)
    assert client.refreshed
    assert "new" not in credential.encrypted_access_token


def test_rate_limit_does_not_mark_success(db_session, connected):
    user, _, _ = connected
    with pytest.raises(StravaRateLimitError):
        sync_strava(db_session, user.id, FakeClient(error=StravaRateLimitError("limit")))


def test_linked_creates_source_link_and_preserves_fit_samples(db_session, connected):
    user, _, _ = connected
    fit = create_fit_activity(db_session, user)
    result = reconcile_strava_activity({"started_at": fit.started_at, **payload(501)}, [fit])
    assert result.decision == "linked"
    stats = sync_strava(db_session, user.id, FakeClient(pages=[[payload(501)]]))
    assert stats["linked"] == 1
    assert stats["created"] == 0
    assert db_session.query(Activity).count() == 1
    assert sample_count(db_session, fit) == 1570
    link = links(db_session)[0]
    assert link.activity_id == fit.id
    assert link.external_id == "501"
    assert link.status == "linked"
    assert link.reconciliation_score == result.score
    assert "candidate_activity_id" in link.reconciliation_evidence_json
    assert "identified" in link.source_data_json


def test_linked_does_not_mix_strava_streams_and_rerun_is_idempotent(db_session, connected):
    user, _, _ = connected
    fit = create_fit_activity(db_session, user)
    client = FakeClient(pages=[[payload(502)]])
    sync_strava(db_session, user.id, client)
    sync_strava(db_session, user.id, client)
    assert db_session.query(Activity).count() == 1
    assert len(links(db_session)) == 1
    assert sample_count(db_session, fit) == 1570


def test_possible_duplicate_creates_separate_activity_with_streams_and_evidence(db_session, connected):
    user, _, _ = connected
    fit = create_fit_activity(db_session, user, samples=0)
    duplicate = payload(601, start_date="2026-07-11T12:51:00Z")
    stats = sync_strava(db_session, user.id, FakeClient(pages=[[duplicate]]))
    assert stats["possible_duplicates"] == 1
    assert db_session.query(Activity).count() == 2
    strava_activity = db_session.scalar(select(Activity).where(Activity.external_id == "601"))
    assert sample_count(db_session, strava_activity) == 3
    link = links(db_session)[0]
    assert link.status == "possible_duplicate"
    assert str(fit.id) in link.reconciliation_evidence_json
    assert link.reconciliation_score is not None


def test_separate_creates_activity_link_and_streams(db_session, connected):
    user, _, _ = connected
    create_fit_activity(db_session, user, samples=0)
    separate = payload(701, start_date="2026-07-11T15:42:05Z")
    stats = sync_strava(db_session, user.id, FakeClient(pages=[[separate]]))
    assert stats["created"] == 1
    strava_activity = db_session.scalar(select(Activity).where(Activity.external_id == "701"))
    assert strava_activity is not None
    assert sample_count(db_session, strava_activity) == 3
    assert links(db_session)[0].status == "separate"


def test_existing_external_id_link_is_idempotent_and_skips_samples(db_session, connected):
    user, _, _ = connected
    sync_strava(db_session, user.id, FakeClient(pages=[[payload(801)]]))
    first_activity_count = db_session.query(Activity).count()
    first_sample_count = db_session.query(ActivitySample).count()
    stats = sync_strava(db_session, user.id, FakeClient(pages=[[payload(801)]]))
    assert stats["skipped"] == 1
    assert db_session.query(Activity).count() == first_activity_count
    assert db_session.query(ActivitySample).count() == first_sample_count
    assert len(links(db_session)) == 1


def test_stream_failure_preserves_summary_and_counts_error(db_session, connected):
    user, _, _ = connected
    stats = sync_strava(
        db_session,
        user.id,
        FakeClient(pages=[[payload(901)]], stream_error=StravaError("temporary")),
    )
    assert stats["created"] == 1
    assert stats["errors"] == 1
    assert db_session.scalar(select(Activity).where(Activity.external_id == "901")) is not None
    assert "Falha ao buscar streams Strava" in links(db_session)[0].source_data_json


def test_authentication_error_requires_reconnect(db_session, connected):
    user, _, _ = connected
    with pytest.raises(StravaAuthenticationError):
        sync_strava(db_session, user.id, FakeClient(error=StravaAuthenticationError("401")))


def test_latlng_is_not_requested_or_persisted(db_session, connected):
    user, _, _ = connected
    client = FakeClient(pages=[[payload(1001)]], streams={**stream_payload(), "latlng": {"data": [[-1, -1]]}})
    sync_strava(db_session, user.id, client)
    assert "latlng" not in links(db_session)[0].source_data_json
    assert db_session.query(ActivitySample).count() == 3


def test_dashboard_query_has_single_primary_run_for_linked_case(db_session, connected):
    user, _, _ = connected
    create_fit_activity(db_session, user)
    sync_strava(db_session, user.id, FakeClient(pages=[[payload(1101)]]))
    primary_runs = db_session.scalars(
        select(Activity).where(
            Activity.user_id == user.id,
            Activity.activity_type == "run",
            Activity.primary_activity_id.is_(None),
        )
    ).all()
    assert len(primary_runs) == 1


def test_sync_commits_progress_and_limits_work_per_run(db_session, connected, monkeypatch):
    user, _, _ = connected
    monkeypatch.setenv("STRAVA_SYNC_MAX_ACTIVITIES_PER_RUN", "2")
    get_settings.cache_clear()
    try:
        stats = sync_strava(
            db_session,
            user.id,
            FakeClient(pages=[[payload(1201), payload(1202), payload(1203)]]),
        )
    finally:
        get_settings.cache_clear()
    assert stats["fetched"] == 2
    state = db_session.scalar(select(IntegrationState))
    assert state is not None
    assert state.status == "connected"
    log = db_session.scalar(select(SyncLog).where(SyncLog.action == "strava_sync"))
    assert log is not None
    assert log.status == "completed"
    assert '"processed": 2' in log.message
    assert '"remaining_in_run": 0' in log.message
