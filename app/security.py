import base64
import hashlib
import hmac
import time

from app.config import get_settings


def _sign(value: str) -> str:
    key = get_settings().app_secret_key.encode("utf-8")
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def make_session_cookie(user_id: int) -> str:
    payload = f"{user_id}:{int(time.time())}"
    encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    return f"{encoded}.{_sign(encoded)}"


def verify_session_cookie(cookie: str | None, max_age_seconds: int = 60 * 60 * 24 * 14) -> int | None:
    if not cookie or "." not in cookie:
        return None
    encoded, signature = cookie.rsplit(".", 1)
    if not hmac.compare_digest(_sign(encoded), signature):
        return None
    try:
        payload = base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
        user_id_text, created_text = payload.split(":", 1)
        if int(time.time()) - int(created_text) > max_age_seconds:
            return None
        return int(user_id_text)
    except (ValueError, UnicodeDecodeError):
        return None


def password_matches(candidate: str) -> bool:
    expected = get_settings().app_local_password
    return hmac.compare_digest(candidate, expected)


def redact_secret(value: str) -> str:
    lowered = value.lower()
    for marker in ("token", "secret", "password", "authorization"):
        if marker in lowered:
            return "[redacted]"
    return value
