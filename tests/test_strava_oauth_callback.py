from datetime import UTC, datetime, timedelta

from starlette.requests import Request

from app import routes
from app.config import get_settings
from app.models import DataSource, OAuthCredential, User
from app.security import make_oauth_state_cookie


class FakeStravaClient:
    def exchange_code(self, code: str) -> dict:
        assert code == "valid-code"
        return {
            "access_token": "access-token-value",
            "refresh_token": "refresh-token-value",
            "expires_at": int((datetime.now(UTC) + timedelta(hours=6)).timestamp()),
            "token_type": "Bearer",
            "athlete": {"id": 12345, "firstname": "Humberto", "lastname": "Pradera"},
        }


def request_with_state(state: str) -> Request:
    cookie = f"hp_strava_state={make_oauth_state_cookie(state)}"
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/integrations/strava/callback",
            "headers": [(b"cookie", cookie.encode("ascii"))],
            "query_string": b"",
        }
    )


def plain_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/integracoes",
            "headers": [],
            "query_string": b"",
        }
    )


def test_strava_callback_accepts_scope_from_callback_and_persists_credential(
    monkeypatch, db_session
):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "mGFWnXyGmDvQHhpbJ4slZOTNupC6lDltJ-NvCRHnF74=")
    monkeypatch.setenv("STRAVA_ENABLED", "true")
    monkeypatch.setenv("STRAVA_CLIENT_ID", "264569")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "client-secret")
    get_settings.cache_clear()
    monkeypatch.setattr(routes, "StravaClient", lambda: FakeStravaClient())

    user = User(name="Humberto")
    db_session.add(user)
    db_session.commit()

    response = routes.strava_callback(
        request_with_state("state-value"),
        code="valid-code",
        state="state-value",
        scope="read,activity:read",
        db=db_session,
        user=user,
    )

    assert response.status_code == 303
    credential = db_session.query(OAuthCredential).one()
    source = db_session.query(DataSource).filter(DataSource.name == "strava").one()
    assert credential.data_source_id == source.id
    assert credential.scopes == "read,activity:read"
    assert credential.expires_at is not None
    assert "access-token-value" not in credential.encrypted_access_token
    assert "refresh-token-value" not in credential.encrypted_refresh_token

    page = routes.integrations_page(plain_request(), db=db_session, user=user)
    body = page.body.decode("utf-8")
    assert "Status: conectado" in body
    assert "Sincronizar agora" in body

    get_settings.cache_clear()
