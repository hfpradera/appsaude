from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Activity, ActivitySample


def persist_strava_streams(db: Session, activity: Activity, streams: dict) -> int:
    """Persist only valid, time-indexed Strava samples; never replaces existing samples."""
    time_stream = (streams.get("time") or {}).get("data")
    if not isinstance(time_stream, list):
        return 0
    values = {name: (item or {}).get("data") or [] for name, item in streams.items()}
    existing = set(
        db.scalars(
            select(ActivitySample.recorded_at).where(ActivitySample.activity_id == activity.id)
        ).all()
    )
    created = 0
    for index, relative in enumerate(time_stream):
        if not isinstance(relative, (int, float)) or relative < 0:
            continue
        recorded_at = activity.started_at + timedelta(seconds=relative)
        if recorded_at in existing:
            continue

        def value(name, index=index):
            data = values.get(name, [])
            return data[index] if index < len(data) else None

        speed = value("velocity_smooth")
        db.add(
            ActivitySample(
                activity_id=activity.id,
                recorded_at=recorded_at,
                heart_rate=value("heartrate"),
                pace_seconds_per_km=1000 / speed
                if isinstance(speed, (int, float)) and speed > 0
                else None,
                cadence=value("cadence"),
                power_watts=value("watts"),
                altitude_meters=value("altitude"),
                speed_mps=speed,
                temperature_c=value("temp"),
            )
        )
        created += 1
    return created
