import json
import logging
from urllib.parse import urlsplit

from app.security import redact_secret


def _sanitize_message(value: object) -> object:
    if not isinstance(value, str):
        return value
    if "/integrations/" in value and "/callback" in value:
        parts = value.split()
        return " ".join(_sanitize_path(part) for part in parts)
    return value


def _sanitize_path(value: str) -> str:
    try:
        split = urlsplit(value)
    except ValueError:
        return value
    if split.path.startswith("/integrations/") and split.path.endswith("/callback"):
        return split.path
    return value


class OAuthAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(_sanitize_message(item) for item in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: _sanitize_message(value) for key, value in record.args.items()}
        record.msg = _sanitize_message(record.msg)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": redact_secret(record.getMessage()),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(OAuthAccessFilter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
