import logging
import threading
import time
from datetime import date

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import DataSource, OAuthCredential, User
from app.services import sync_locks
from app.services.strava import StravaError
from app.services.sync import sync_strava
from app.services.whoop import WhoopError
from app.services.whoop_sync import sync_whoop

logger = logging.getLogger(__name__)
_started = False


def start_sync_scheduler() -> None:
    global _started
    settings = get_settings()
    if _started or not settings.auto_sync_enabled:
        return
    _started = True
    thread = threading.Thread(target=_scheduler_loop, name="sync-scheduler", daemon=True)
    thread.start()


def _scheduler_loop() -> None:
    ran_slots: set[tuple[date, int, str]] = set()
    while True:
        settings = get_settings()
        today = date.today()
        hour = time.localtime().tm_hour
        strava_hours = _hours(settings.auto_sync_hours)
        whoop_hours = set(strava_hours)
        if settings.whoop_auto_sync_enabled:
            whoop_hours.add(settings.whoop_auto_sync_hour)
        try:
            if hour in strava_hours and (today, hour, "strava") not in ran_slots:
                _run_once("strava")
                ran_slots.add((today, hour, "strava"))
            if hour in whoop_hours and (today, hour, "whoop") not in ran_slots:
                _run_once("whoop")
                ran_slots.add((today, hour, "whoop"))
            ran_slots = {slot for slot in ran_slots if slot[0] == today}
        except Exception:
            logger.exception("Sincronizacao automatica falhou.")
        time.sleep(300)


def _hours(raw: str) -> set[int]:
    values: set[int] = set()
    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            hour = int(item)
        except ValueError:
            continue
        if 0 <= hour <= 23:
            values.add(hour)
    return values or {6, 14, 22}


def _run_once(source_name: str) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).limit(1))
        if not user:
            return
        _sync_source(db, user.id, source_name)


def _sync_source(db, user_id: int, source_name: str) -> None:
    source = db.scalar(select(DataSource).where(DataSource.name == source_name))
    if not source:
        return
    credential = db.scalar(
        select(OAuthCredential).where(
            OAuthCredential.user_id == user_id,
            OAuthCredential.data_source_id == source.id,
        )
    )
    if not credential:
        return
    if not sync_locks.try_start(source_name):
        return
    try:
        if source_name == "strava":
            sync_strava(db, user_id)
        elif source_name == "whoop":
            sync_whoop(db, user_id)
    except (StravaError, WhoopError) as exc:
        logger.warning("Sincronizacao %s falhou: %s", source_name, exc)
    finally:
        sync_locks.finish(source_name)
