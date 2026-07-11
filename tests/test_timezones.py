from datetime import UTC, datetime

from app.services.timezone import local_date, to_local


def test_utc_to_sao_paulo_date():
    value = datetime(2026, 7, 11, 3, 30, tzinfo=UTC)
    local = to_local(value, "America/Sao_Paulo")
    assert local.hour == 0
    assert local_date(value, "America/Sao_Paulo").isoformat() == "2026-07-11"
