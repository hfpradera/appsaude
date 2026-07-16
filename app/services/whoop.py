import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import IntegrationState, OAuthCredential
from app.services.strava import decrypt_token, encrypt_token
from app.services.timezone import utc_now

AUTHORIZE_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
API_URL = "https://api.prod.whoop.com/developer/v2"
WHOOP_SCOPES = (
    "offline read:profile read:recovery read:cycles read:sleep read:workout "
    "read:body_measurement"
)


class WhoopError(RuntimeError):
    pass


class WhoopAuthenticationError(WhoopError):
    pass


class WhoopRateLimitError(WhoopError):
    pass


class WhoopPermanentError(WhoopError):
    pass


def authorization_url(state: str) -> str:
    settings = get_settings()
    if not settings.whoop_configured:
        raise RuntimeError("WHOOP nao configurado.")
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.whoop_client_id,
            "redirect_uri": settings.effective_whoop_redirect_uri,
            "scope": WHOOP_SCOPES,
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


class WhoopClient:
    def __init__(self, client: httpx.Client | None = None) -> None:
        timeout = float(get_settings().whoop_http_timeout_seconds)
        self.client = client or httpx.Client(timeout=httpx.Timeout(timeout))

    def exchange_code(self, code: str) -> dict:
        return self._request(
            "POST",
            TOKEN_URL,
            data=self._credentials(
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": get_settings().effective_whoop_redirect_uri,
                }
            ),
        )

    def refresh(self, refresh_token: str) -> dict:
        return self._request(
            "POST",
            TOKEN_URL,
            data=self._credentials(
                {"grant_type": "refresh_token", "refresh_token": refresh_token}
            ),
        )

    def profile(self, access_token: str) -> dict:
        data = self._request("GET", f"{API_URL}/user/profile/basic", token=access_token)
        return data if isinstance(data, dict) else {}

    def recoveries(self, access_token: str, start: datetime | None = None) -> Iterator[dict]:
        yield from self._paginated(f"{API_URL}/recovery", access_token, start=start)

    def cycles(self, access_token: str, start: datetime | None = None) -> Iterator[dict]:
        yield from self._paginated(f"{API_URL}/cycle", access_token, start=start)

    def sleeps(self, access_token: str, start: datetime | None = None) -> Iterator[dict]:
        yield from self._paginated(f"{API_URL}/activity/sleep", access_token, start=start)

    def workouts(self, access_token: str, start: datetime | None = None) -> Iterator[dict]:
        yield from self._paginated(f"{API_URL}/activity/workout", access_token, start=start)

    def body_measurements(self, access_token: str, start: datetime | None = None) -> Iterator[dict]:
        yield from self._paginated(f"{API_URL}/user/measurement/body", access_token, start=start)

    def _paginated(
        self, url: str, access_token: str, start: datetime | None = None
    ) -> Iterator[dict]:
        next_token: str | None = None
        while True:
            params: dict[str, str | int] = {"limit": get_settings().whoop_sync_page_limit}
            if start:
                params["start"] = start.astimezone(UTC).isoformat().replace("+00:00", "Z")
            if next_token:
                params["nextToken"] = next_token
            data = self._request("GET", url, token=access_token, params=params)
            if isinstance(data, list):
                records = data
                next_token = None
            elif isinstance(data, dict):
                records = data.get("records") or []
                next_token = data.get("next_token") or data.get("nextToken")
            else:
                records = []
                next_token = None
            yield from [record for record in records if isinstance(record, dict)]
            if not next_token:
                return

    def _credentials(self, extra: dict[str, str]) -> dict[str, str]:
        settings = get_settings()
        if not settings.whoop_configured:
            raise WhoopPermanentError("WHOOP nao configurado.")
        return {
            "client_id": settings.whoop_client_id,
            "client_secret": settings.whoop_client_secret,
            **extra,
        }

    def _request(self, method: str, url: str, token: str | None = None, **kwargs: object) -> dict | list:
        headers = {"Authorization": f"Bearer {token}"} if token else None
        for attempt in range(3):
            try:
                response = self.client.request(method, url, headers=headers, **kwargs)
            except httpx.TimeoutException as exc:
                if attempt == 2:
                    raise WhoopError("Timeout ao consultar WHOOP.") from exc
                time.sleep(2**attempt)
                continue
            if response.status_code == 401:
                raise WhoopAuthenticationError("Credencial WHOOP invalida ou expirada.")
            if response.status_code == 429:
                raise WhoopRateLimitError("Limite de requisicoes WHOOP atingido.")
            if response.status_code in {400, 403, 404}:
                raise WhoopPermanentError(f"WHOOP recusou a operacao ({response.status_code}).")
            if response.status_code >= 400:
                raise WhoopError(f"WHOOP retornou erro HTTP {response.status_code}.")
            if response.status_code >= 500:
                if attempt == 2:
                    raise WhoopError("Falha temporaria no WHOOP.")
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
            return response.json()
        raise WhoopError("Falha inesperada ao consultar WHOOP.")


def token_expiring(credential: OAuthCredential) -> bool:
    if credential.expires_at is None:
        return True
    margin = timedelta(seconds=get_settings().whoop_refresh_margin_seconds)
    expires_at = credential.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= utc_now() + margin


def fresh_access_token(
    db: Session,
    credential: OAuthCredential,
    integration_state: IntegrationState | None,
    client: WhoopClient | None = None,
) -> str:
    if not token_expiring(credential):
        return decrypt_token(credential.encrypted_access_token)
    if not credential.encrypted_refresh_token:
        _mark_reconnect(db, integration_state, "Refresh token WHOOP ausente.")
        raise WhoopAuthenticationError("WHOOP precisa reconectar.")
    whoop = client or WhoopClient()
    try:
        tokens = whoop.refresh(decrypt_token(credential.encrypted_refresh_token))
    except WhoopAuthenticationError:
        _mark_reconnect(db, integration_state, "Refresh token WHOOP invalido ou revogado.")
        raise
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not access_token:
        _mark_reconnect(db, integration_state, "WHOOP nao retornou access token.")
        raise WhoopAuthenticationError("WHOOP precisa reconectar.")
    credential.encrypted_access_token = encrypt_token(access_token)
    if refresh_token:
        credential.encrypted_refresh_token = encrypt_token(refresh_token)
    if tokens.get("expires_at"):
        credential.expires_at = datetime.fromtimestamp(int(tokens["expires_at"]), UTC)
    elif tokens.get("expires_in"):
        credential.expires_at = utc_now() + timedelta(seconds=int(tokens["expires_in"]))
    credential.token_type = tokens.get("token_type") or credential.token_type
    db.add(credential)
    if integration_state:
        integration_state.status = "connected"
        integration_state.last_error = None
        db.add(integration_state)
    db.commit()
    return access_token


def _mark_reconnect(
    db: Session, integration_state: IntegrationState | None, message: str
) -> None:
    if integration_state:
        integration_state.status = "needs_reconnect"
        integration_state.last_error = message
        db.add(integration_state)
        db.commit()


def whoop_credentials(
    db: Session, user_id: int, data_source_id: int
) -> tuple[OAuthCredential | None, IntegrationState | None]:
    credential = db.scalar(
        select(OAuthCredential).where(
            OAuthCredential.user_id == user_id,
            OAuthCredential.data_source_id == data_source_id,
        )
    )
    state = db.scalar(
        select(IntegrationState).where(
            IntegrationState.user_id == user_id,
            IntegrationState.data_source_id == data_source_id,
        )
    )
    return credential, state
