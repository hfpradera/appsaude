from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Activity,
    ActivityLap,
    ActivitySample,
    DailyRecovery,
    DataSource,
    Sleep,
    SubjectiveCheckin,
    User,
)


def ensure_user(db: Session) -> User:
    user = db.scalar(select(User).limit(1))
    if user:
        return user
    user = User(name="Humberto", timezone="America/Sao_Paulo")
    db.add(user)
    db.flush()
    return user


def seed_demo_data(db: Session) -> None:
    user = ensure_user(db)
    if db.scalar(select(Activity).where(Activity.user_id == user.id).limit(1)):
        db.commit()
        return
    source = DataSource(name="demo", kind="manual")
    db.add(source)
    db.flush()
    today = date.today()
    for offset, score, hrv, resting, strain, sleep_hours in [
        (0, 72, 61, 48, 8.2, 7.4),
        (1, 65, 58, 50, 11.5, 6.8),
        (2, 42, 49, 55, 14.1, 5.9),
        (3, 78, 63, 47, 5.0, 8.1),
        (4, 70, 59, 49, 7.2, 7.0),
        (5, 55, 53, 52, 10.0, 6.4),
        (6, 82, 66, 46, 6.0, 7.8),
    ]:
        day = today - timedelta(days=offset)
        db.add(DailyRecovery(user_id=user.id, data_source_id=source.id, day=day, recovery_score=score, hrv_ms=hrv, resting_hr=resting, daily_strain=strain))
        db.add(Sleep(user_id=user.id, data_source_id=source.id, day=day, sleep_duration_seconds=int(sleep_hours * 3600), sleep_need_seconds=8 * 3600, efficiency_percent=88 - offset, consistency_percent=80 + offset, cycles=4))
    starts = [
        (today - timedelta(days=1), "corrida", 5400, 12000, 142, 169),
        (today - timedelta(days=3), "bike", 4200, 23000, 128, 154),
        (today - timedelta(days=5), "corrida", 2700, 6500, 136, 158),
    ]
    for day, kind, duration, distance, avg_hr, max_hr in starts:
        start = datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(hour=10)
        activity = Activity(
            user_id=user.id,
            data_source_id=source.id,
            external_id=f"demo-{kind}-{day.isoformat()}",
            activity_type=kind,
            started_at=start,
            ended_at=start + timedelta(seconds=duration),
            total_duration_seconds=duration,
            moving_time_seconds=duration - 120,
            distance_meters=distance,
            avg_pace_seconds_per_km=duration / (distance / 1000),
            avg_hr=avg_hr,
            max_hr=max_hr,
            calories=650,
            elevation_gain_meters=120,
            strain=9.5,
            notes="Dado ficticio para demonstracao.",
        )
        db.add(activity)
        db.flush()
        for lap_index in range(1, 4):
            db.add(ActivityLap(activity_id=activity.id, lap_index=lap_index, duration_seconds=duration / 3, distance_meters=distance / 3, avg_hr=avg_hr + lap_index))
        for sample_index in range(20):
            db.add(
                ActivitySample(
                    activity_id=activity.id,
                    recorded_at=start + timedelta(seconds=sample_index * duration / 20),
                    heart_rate=avg_hr - 8 + sample_index,
                    pace_seconds_per_km=activity.avg_pace_seconds_per_km,
                    cadence=168 + sample_index % 4,
                    power_watts=220 + sample_index,
                    altitude_meters=700 + sample_index,
                    speed_mps=(distance / duration),
                )
            )
    db.add(SubjectiveCheckin(user_id=user.id, day=today, perceived_effort=3, sleep_quality=7, energy=7, muscle_soreness=2, pain_regions="", mood="bem", caffeine_amount="2 cafes", last_caffeine_at="14:00", alcohol="nao", food_near_sleep="leve", notes="Dado ficticio."))
    db.commit()
