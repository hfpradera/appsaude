import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Activity,
    DailyRecovery,
    DataSource,
    ExternalRecord,
    IntegrationState,
    Sleep,
    SyncLog,
)
from app.services.timezone import utc_now
from app.services.whoop import (
    WhoopAuthenticationError,
    WhoopClient,
    WhoopError,
    WhoopRateLimitError,
    fresh_access_token,
    whoop_credentials,
)

logger = logging.getLogger(__name__)


def sync_whoop(db: Session, user_id: int, client: WhoopClient | None = None) -> SyncLog:
    source = db.scalar(select(DataSource).where(DataSource.name == "whoop")) or DataSource(
        name="whoop", kind="oauth"
    )
    db.add(source)
    db.flush()
    credential, integration_state = whoop_credentials(db, user_id, source.id)
    if not credential:
        raise WhoopAuthenticationError("WHOOP nao conectado.")
    if integration_state is None:
        integration_state = IntegrationState(user_id=user_id, data_source_id=source.id)
        db.add(integration_state)
        db.flush()

    started_at = utc_now()
    sync_log = SyncLog(
        data_source_id=source.id,
        action="whoop_sync",
        status="running",
        message=json.dumps({"started_at": started_at.isoformat()}),
    )
    db.add(sync_log)
    db.commit()

    counts = {
        "recovery": 0,
        "sleep": 0,
        "cycles": 0,
        "workouts": 0,
        "body_measurements": 0,
        "created": 0,
        "updated": 0,
        "errors": 0,
    }
    lookback_start = _sync_start(integration_state)
    whoop = client or WhoopClient()
    try:
        access_token = fresh_access_token(db, credential, integration_state, whoop)
        _update_progress(db, sync_log, counts, "recovery")
        _sync_records(
            whoop.recoveries(access_token, start=lookback_start),
            lambda record: _upsert_recovery(db, user_id, source.id, record),
            counts,
            "recovery",
        )
        _update_progress(db, sync_log, counts, "sleep")
        _sync_records(
            whoop.sleeps(access_token, start=lookback_start),
            lambda record: _upsert_sleep(db, user_id, source.id, record),
            counts,
            "sleep",
        )
        _update_progress(db, sync_log, counts, "cycles")
        _sync_records(
            whoop.cycles(access_token, start=lookback_start),
            lambda record: _upsert_external_record(db, user_id, source.id, "cycle", record),
            counts,
            "cycles",
        )
        _update_progress(db, sync_log, counts, "workouts")
        _sync_records(
            whoop.workouts(access_token, start=lookback_start),
            lambda record: _upsert_workout(db, user_id, source.id, record),
            counts,
            "workouts",
        )
        _update_progress(db, sync_log, counts, "body_measurements")
        _sync_records(
            whoop.body_measurements(access_token, start=lookback_start),
            lambda record: _upsert_external_record(
                db, user_id, source.id, "body_measurement", record
            ),
            counts,
            "body_measurements",
        )
    except WhoopRateLimitError as exc:
        _finish_sync(db, sync_log, integration_state, "rate_limited", counts, str(exc), False)
        raise
    except WhoopAuthenticationError as exc:
        _finish_sync(db, sync_log, integration_state, "auth_failed", counts, str(exc), False)
        raise
    except WhoopError as exc:
        _finish_sync(db, sync_log, integration_state, "failed", counts, str(exc), False)
        raise
    except Exception as exc:
        logger.exception("Sincronizacao WHOOP falhou.")
        _finish_sync(
            db,
            sync_log,
            integration_state,
            "failed",
            counts,
            f"Falha inesperada na sincronizacao WHOOP: {exc.__class__.__name__}",
            False,
        )
        raise

    _finish_sync(db, sync_log, integration_state, "completed", counts, None, True)
    return sync_log


def _sync_start(integration_state: IntegrationState) -> datetime:
    if integration_state.last_synced_at:
        return integration_state.last_synced_at.astimezone(UTC) - timedelta(days=1)
    return utc_now() - timedelta(days=get_settings().whoop_sync_lookback_days)


def _sync_records(
    records: Any,
    upsert: Any,
    counts: dict[str, int],
    counter_name: str,
) -> None:
    for record in records:
        try:
            was_created = upsert(record)
            counts[counter_name] += 1
            if was_created:
                counts["created"] += 1
            else:
                counts["updated"] += 1
        except (TypeError, ValueError, KeyError):
            counts["errors"] += 1


def _upsert_recovery(db: Session, user_id: int, source_id: int, record: dict) -> bool:
    score = _dict(record.get("score"))
    day = _day_from_record(record)
    recovery = db.scalar(
        select(DailyRecovery).where(
            DailyRecovery.user_id == user_id,
            DailyRecovery.data_source_id == source_id,
            DailyRecovery.day == day,
        )
    )
    created = recovery is None
    if recovery is None:
        recovery = DailyRecovery(user_id=user_id, data_source_id=source_id, day=day)
    recovery.recovery_score = _number(score.get("recovery_score"))
    recovery.hrv_ms = _number(score.get("hrv_rmssd_milli"))
    recovery.resting_hr = _number(score.get("resting_heart_rate"))
    recovery.respiratory_rate = _number(score.get("respiratory_rate"))
    recovery.skin_temperature_c = _number(score.get("skin_temp_celsius"))
    recovery.notes = _json(record)
    db.add(recovery)
    db.commit()
    return created


def _upsert_sleep(db: Session, user_id: int, source_id: int, record: dict) -> bool:
    external_id = _external_id(record)
    started_at = _parse_dt(record.get("start"))
    ended_at = _parse_dt(record.get("end"))
    day = _day_from_record(record)
    existing = _find_external_record(db, source_id, "sleep", external_id)
    sleep = None
    if existing and existing.data_json:
        saved = json.loads(existing.data_json)
        sleep_id = saved.get("_sleep_row_id")
        if sleep_id:
            sleep = db.get(Sleep, int(sleep_id))
    created = sleep is None
    if sleep is None:
        sleep = Sleep(user_id=user_id, data_source_id=source_id, day=day)
    score = _dict(record.get("score"))
    stage_summary = _dict(score.get("stage_summary"))
    sleep_needed = _dict(score.get("sleep_needed"))
    sleep.day = day
    sleep.started_at = started_at
    sleep.ended_at = ended_at
    sleep.sleep_duration_seconds = _milliseconds_to_seconds(
        stage_summary.get("total_sleep_time_milli")
        or stage_summary.get("total_in_bed_time_milli")
    )
    sleep.sleep_need_seconds = _milliseconds_to_seconds(
        sleep_needed.get("baseline_milli") or sleep_needed.get("need_from_strain_milli")
    )
    sleep.efficiency_percent = _number(score.get("sleep_efficiency_percentage"))
    sleep.consistency_percent = _number(score.get("sleep_consistency_percentage"))
    sleep.respiratory_rate = _number(score.get("respiratory_rate"))
    sleep.skin_temperature_c = _number(score.get("skin_temp_celsius"))
    sleep.notes = _json(record)
    db.add(sleep)
    db.flush()
    _upsert_external_record(
        db,
        user_id,
        source_id,
        "sleep",
        {**record, "_sleep_row_id": sleep.id},
        external_id=external_id,
        recorded_at=started_at,
        day=day,
        commit=False,
    )
    db.commit()
    return created


def _upsert_workout(db: Session, user_id: int, source_id: int, record: dict) -> bool:
    external_id = _external_id(record)
    activity = db.scalar(
        select(Activity).where(
            Activity.data_source_id == source_id,
            Activity.external_id == external_id,
        )
    )
    created = activity is None
    start = _parse_dt(record.get("start")) or utc_now()
    end = _parse_dt(record.get("end"))
    score = _dict(record.get("score"))
    if activity is None:
        activity = Activity(
            user_id=user_id,
            data_source_id=source_id,
            external_id=external_id,
            activity_type=_activity_type(record),
            started_at=start,
        )
    activity.activity_type = _activity_type(record)
    activity.started_at = start
    activity.ended_at = end
    activity.total_duration_seconds = _duration_seconds(start, end)
    activity.avg_hr = _number(score.get("average_heart_rate"))
    activity.max_hr = _number(score.get("max_heart_rate"))
    activity.calories = _number(score.get("kilojoule"))
    activity.strain = _number(score.get("strain"))
    activity.notes = _json(record)
    db.add(activity)
    db.commit()
    return created


def _upsert_external_record(
    db: Session,
    user_id: int,
    source_id: int,
    kind: str,
    record: dict,
    external_id: str | None = None,
    recorded_at: datetime | None = None,
    day: date | None = None,
    commit: bool = True,
) -> bool:
    external_id = external_id or _external_id(record)
    external = _find_external_record(db, source_id, kind, external_id)
    created = external is None
    if external is None:
        external = ExternalRecord(
            user_id=user_id,
            data_source_id=source_id,
            kind=kind,
            external_id=external_id,
        )
    external.recorded_at = recorded_at or _parse_dt(record.get("start")) or _parse_dt(record.get("created_at"))
    external.day = day or _day_from_record(record, allow_today=True)
    external.data_json = _json(record)
    db.add(external)
    if commit:
        db.commit()
    return created


def _find_external_record(
    db: Session, source_id: int, kind: str, external_id: str
) -> ExternalRecord | None:
    return db.scalar(
        select(ExternalRecord).where(
            ExternalRecord.data_source_id == source_id,
            ExternalRecord.kind == kind,
            ExternalRecord.external_id == external_id,
        )
    )


def _finish_sync(
    db: Session,
    sync_log: SyncLog,
    integration_state: IntegrationState,
    status: str,
    counts: dict[str, int],
    error: str | None,
    success: bool,
) -> None:
    now = utc_now()
    sync_log.status = status
    sync_log.finished_at = now
    payload = {**counts}
    if error:
        payload["error"] = error
    sync_log.message = json.dumps(payload, sort_keys=True)
    integration_state.status = "connected" if success else status
    integration_state.last_error = error
    integration_state.last_imported_count = counts["created"] + counts["updated"]
    if success:
        integration_state.last_synced_at = now
        integration_state.sync_cursor_at = now
    db.add(sync_log)
    db.add(integration_state)
    db.commit()


def _update_progress(
    db: Session,
    sync_log: SyncLog,
    counts: dict[str, int],
    phase: str,
) -> None:
    sync_log.message = json.dumps({"phase": phase, **counts}, sort_keys=True)
    db.add(sync_log)
    db.commit()


def _external_id(record: dict) -> str:
    value = record.get("id") or record.get("cycle_id") or record.get("sleep_id")
    if value in (None, ""):
        raise ValueError("Registro WHOOP sem id externo.")
    return str(value)


def _day_from_record(record: dict, allow_today: bool = False) -> date:
    for key in ("date", "day"):
        if record.get(key):
            return date.fromisoformat(str(record[key])[:10])
    for key in ("start", "created_at", "updated_at"):
        value = _parse_dt(record.get(key))
        if value:
            return value.date()
    if allow_today:
        return utc_now().date()
    raise ValueError("Registro WHOOP sem data.")


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _duration_seconds(start: datetime | None, end: datetime | None) -> int | None:
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds()))


def _milliseconds_to_seconds(value: object) -> int | None:
    number = _number(value)
    return int(number / 1000) if number is not None else None


def _number(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _activity_type(record: dict) -> str:
    return str(record.get("sport_name") or record.get("sport_id") or "workout")


def _json(record: dict) -> str:
    return json.dumps(record, ensure_ascii=True, sort_keys=True)
