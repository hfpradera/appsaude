from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    Activity,
    DailyRecovery,
    DataSource,
    ExternalRecord,
    OAuthCredential,
    Sleep,
    User,
)
from app.services.strava import decrypt_token, encrypt_token
from app.services.whoop import authorization_url, fresh_access_token
from app.services.whoop_sync import sync_whoop


class FakeWhoopClient:
    def __init__(self, refresh_response=None):
        self.refresh_response = refresh_response or {}
        self.refreshed = False
        self.after_values = []

    def refresh(self, _token):
        self.refreshed = True
        return self.refresh_response

    def recoveries(self, _token, start=None):
        self.after_values.append(start)
        return [
            {
                "id": "recovery-1",
                "date": "2026-07-11",
                "score": {
                    "recovery_score": 83,
                    "hrv_rmssd_milli": 47.5,
                    "resting_heart_rate": 52,
                    "respiratory_rate": 14.2,
                    "skin_temp_celsius": 33.1,
                },
            }
        ]

    def sleeps(self, _token, start=None):
        self.after_values.append(start)
        return [
            {
                "id": "sleep-1",
                "start": "2026-07-10T23:30:00Z",
                "end": "2026-07-11T07:00:00Z",
                "score": {
                    "sleep_efficiency_percentage": 91,
                    "sleep_consistency_percentage": 78,
                    "stage_summary": {"total_sleep_time_milli": 25200000},
                    "sleep_needed": {"baseline_milli": 28800000},
                },
            }
        ]

    def cycles(self, _token, start=None):
        self.after_values.append(start)
        return [{"id": "cycle-1", "start": "2026-07-11T00:00:00Z", "score": {"strain": 10.4}}]

    def workouts(self, _token, start=None):
        self.after_values.append(start)
        return [
            {
                "id": "workout-1",
                "sport_name": "Running",
                "start": "2026-07-11T12:42:00Z",
                "end": "2026-07-11T13:08:00Z",
                "score": {
                    "average_heart_rate": 157,
                    "max_heart_rate": 176,
                    "kilojoule": 420,
                    "strain": 9.1,
                },
            }
        ]

    def body_measurements(self, _token, start=None):
        self.after_values.append(start)
        return [{"id": "body-1", "created_at": "2026-07-11T08:00:00Z", "weight_kilogram": 75}]


@pytest.fixture()
def whoop_env(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "mGFWnXyGmDvQHhpbJ4slZOTNupC6lDltJ-NvCRHnF74=")
    monkeypatch.setenv("WHOOP_CLIENT_ID", "client-id")
    monkeypatch.setenv("WHOOP_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("WHOOP_REDIRECT_URI", "https://saude.hfpradera.com.br/integrations/whoop/callback")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def connected(db_session, whoop_env):
    user = User(name="Humberto")
    source = DataSource(name="whoop", kind="oauth")
    db_session.add_all([user, source])
    db_session.flush()
    credential = OAuthCredential(
        user_id=user.id,
        data_source_id=source.id,
        encrypted_access_token=encrypt_token("access"),
        encrypted_refresh_token=encrypt_token("refresh"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes="offline read:profile read:recovery read:cycles read:sleep read:workout read:body_measurement",
    )
    db_session.add(credential)
    db_session.commit()
    return user, source, credential


def test_authorization_url_contains_required_whoop_scope(whoop_env):
    url = authorization_url("state-123")
    assert "response_type=code" in url
    assert "client_id=client-id" in url
    assert "state=state-123" in url
    assert "read%3Arecovery" in url
    assert "read%3Abody_measurement" in url


def test_valid_token_does_not_refresh(db_session, connected):
    user, _, credential = connected
    client = FakeWhoopClient()
    assert fresh_access_token(db_session, credential, None, client) == "access"
    assert not client.refreshed
    assert user.id


def test_naive_sqlite_expiration_is_treated_as_utc(db_session, connected):
    _, _, credential = connected
    credential.expires_at = (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None)
    client = FakeWhoopClient()
    assert fresh_access_token(db_session, credential, None, client) == "access"
    assert not client.refreshed


def test_expiring_token_refreshes_and_stays_encrypted(db_session, connected):
    _, _, credential = connected
    credential.expires_at = datetime.now(UTC)
    client = FakeWhoopClient(
        refresh_response={
            "access_token": "new-access",
            "refresh_token": "rotated-refresh",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
    )
    assert fresh_access_token(db_session, credential, None, client) == "new-access"
    assert client.refreshed
    assert "new-access" not in credential.encrypted_access_token
    assert decrypt_token(credential.encrypted_refresh_token) == "rotated-refresh"


def test_sync_imports_all_whoop_record_types_idempotently(db_session, connected):
    user, source, _ = connected
    client = FakeWhoopClient()
    sync_whoop(db_session, user.id, client)
    sync_whoop(db_session, user.id, client)

    assert db_session.query(DailyRecovery).filter_by(data_source_id=source.id).count() == 1
    assert db_session.query(Sleep).filter_by(data_source_id=source.id).count() == 1
    assert db_session.query(Activity).filter_by(data_source_id=source.id).count() == 1
    assert (
        db_session.query(ExternalRecord)
        .filter(ExternalRecord.data_source_id == source.id, ExternalRecord.kind == "cycle")
        .count()
        == 1
    )
    assert (
        db_session.query(ExternalRecord)
        .filter(
            ExternalRecord.data_source_id == source.id,
            ExternalRecord.kind == "body_measurement",
        )
        .count()
        == 1
    )
    activity = db_session.scalar(select(Activity).where(Activity.external_id == "workout-1"))
    assert activity.avg_hr == 157


def test_sync_uses_last_sync_as_lookback(db_session, connected):
    user, source, _ = connected
    from app.models import IntegrationState

    state = IntegrationState(
        user_id=user.id,
        data_source_id=source.id,
        status="connected",
        last_synced_at=datetime(2026, 7, 12, tzinfo=UTC),
    )
    db_session.add(state)
    db_session.commit()
    client = FakeWhoopClient()
    sync_whoop(db_session, user.id, client)
    assert client.after_values[0] == datetime(2026, 7, 11, tzinfo=UTC)
