from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return ensure_utc(value) if isinstance(value, datetime) else None
    normalized = value.strip().replace("Z", "+00:00")
    return ensure_utc(datetime.fromisoformat(normalized))


def to_local(value: datetime | None, timezone_name: str = "America/Sao_Paulo") -> datetime | None:
    if value is None:
        return None
    return ensure_utc(value).astimezone(ZoneInfo(timezone_name))


def local_date(value: datetime, timezone_name: str = "America/Sao_Paulo") -> date:
    return to_local(value, timezone_name).date()  # type: ignore[union-attr]


def seconds_to_human(seconds: int | float | None) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, _ = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}min"
    return f"{minutes}min"
