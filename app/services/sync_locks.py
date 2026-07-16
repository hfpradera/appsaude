from __future__ import annotations

import threading

_locks: dict[str, threading.Lock] = {"strava": threading.Lock(), "whoop": threading.Lock()}


def try_start(source: str) -> bool:
    """Non-blocking attempt to claim the sync lock for `source`. True if claimed."""
    return _locks[source].acquire(blocking=False)


def finish(source: str) -> None:
    """Release the sync lock for `source`. Safe even if already released."""
    try:
        _locks[source].release()
    except RuntimeError:
        pass


def is_running(source: str) -> bool:
    return _locks[source].locked()
