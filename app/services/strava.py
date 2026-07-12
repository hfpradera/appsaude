import secrets
import time
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

REQUIRED_SCOPE = {"activity:read"}
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API_URL = "https://www.strava.com/api/v3"


class StravaError(RuntimeError):
    pass


class StravaAuthenticationError(StravaError):
    pass


class StravaRateLimitError(StravaError):
    pass


class StravaPermanentError(StravaError):
    pass


def generate_token_encryption_key() -> str:
    return Fernet.generate_key().decode("ascii")


def token_cipher() -> Fernet:
    key = get_settings().token_encryption_key
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY nao configurada.")
    try:
        return Fernet(key.encode("ascii"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY invalida.") from exc


def encrypt_token(value: str) -> str:
    return token_cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_token(value: str) -> str:
    try:
        return token_cipher().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise RuntimeError("Token criptografado invalido.") from exc


def make_oauth_state() -> str:
    return secrets.token_urlsafe(32)


def authorization_url(state: str) -> str:
    settings = get_settings()
    if not settings.strava_configured:
        raise RuntimeError("Strava nao configurado.")
    query = urlencode({"client_id": settings.strava_client_id, "redirect_uri": settings.effective_strava_redirect_uri, "response_type": "code", "approval_prompt": "auto", "scope": "activity:read", "state": state})
    return f"{AUTHORIZE_URL}?{query}"


def scopes_are_sufficient(scope: str | None) -> bool:
    return REQUIRED_SCOPE.issubset(set((scope or "").replace(",", " ").split()))


class StravaClient:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=httpx.Timeout(15.0))

    def exchange_code(self, code: str) -> dict:
        return self._request("POST", TOKEN_URL, data=self._credentials({"code": code, "grant_type": "authorization_code"}))

    def refresh(self, refresh_token: str) -> dict:
        return self._request("POST", TOKEN_URL, data=self._credentials({"refresh_token": refresh_token, "grant_type": "refresh_token"}))

    def athlete(self, access_token: str) -> dict:
        return self._request("GET", f"{API_URL}/athlete", token=access_token)

    def activities(self, access_token: str, after: datetime | None = None) -> list[dict]:
        items: list[dict] = []
        page = 1
        while True:
            params = {"page": page, "per_page": 100}
            if after:
                params["after"] = int(after.astimezone(UTC).timestamp())
            batch = self._request("GET", f"{API_URL}/athlete/activities", token=access_token, params=params)
            items.extend(batch)
            if len(batch) < 100:
                return items
            page += 1

    def activity_streams(self, access_token: str, activity_id: str | int) -> dict:
        streams = self._request("GET", f"{API_URL}/activities/{activity_id}/streams", token=access_token, params={"keys": "time,distance,heartrate,velocity_smooth,cadence,watts,altitude,temp,moving,grade_smooth", "key_by_type": "true"})
        return streams if isinstance(streams, dict) else {}

    def _credentials(self, extra: dict[str, str]) -> dict[str, str]:
        settings = get_settings()
        if not settings.strava_configured:
            raise StravaPermanentError("Strava nao configurado.")
        return {"client_id": settings.strava_client_id, "client_secret": settings.strava_client_secret, **extra}

    def _request(self, method: str, url: str, token: str | None = None, **kwargs: object) -> dict | list:
        headers = {"Authorization": f"Bearer {token}"} if token else None
        for attempt in range(3):
            try:
                response = self.client.request(method, url, headers=headers, **kwargs)
            except httpx.TimeoutException as exc:
                if attempt == 2:
                    raise StravaError("Timeout ao consultar Strava.") from exc
                time.sleep(2**attempt)
                continue
            if response.status_code == 401:
                raise StravaAuthenticationError("Credencial Strava invalida ou expirada.")
            if response.status_code == 429:
                raise StravaRateLimitError("Limite de requisicoes Strava atingido.")
            if response.status_code in {403, 404}:
                raise StravaPermanentError(f"Strava recusou a operacao ({response.status_code}).")
            if response.status_code >= 500:
                if attempt == 2:
                    raise StravaError("Falha temporaria no Strava.")
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
            return response.json()
        raise StravaError("Falha inesperada ao consultar Strava.")
