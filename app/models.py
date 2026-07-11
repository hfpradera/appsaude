from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.services.timezone import utc_now


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="Humberto")
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timezone: Mapped[str] = mapped_column(String(80), default="America/Sao_Paulo")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    activities: Mapped[list["Activity"]] = relationship(back_populates="user")


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(40), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OAuthCredential(Base):
    __tablename__ = "oauth_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"))
    encrypted_access_token: Mapped[str] = mapped_column(Text)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (
        UniqueConstraint("data_source_id", "external_id", name="uq_activity_source_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    activity_type: Mapped[str] = mapped_column(String(80), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    moving_time_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    distance_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_pace_seconds_per_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_speed_mps: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    cadence: Mapped[float | None] = mapped_column(Float, nullable=True)
    power_watts: Mapped[float | None] = mapped_column(Float, nullable=True)
    calories: Mapped[float | None] = mapped_column(Float, nullable=True)
    elevation_gain_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    strain: Mapped[float | None] = mapped_column(Float, nullable=True)
    hr_zones_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_activity_id: Mapped[int | None] = mapped_column(ForeignKey("activities.id"), nullable=True)
    duplicate_status: Mapped[str] = mapped_column(String(40), default="unique")
    duplicate_decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="activities")
    data_source: Mapped[DataSource] = relationship()
    laps: Mapped[list["ActivityLap"]] = relationship(back_populates="activity", cascade="all, delete-orphan")
    samples: Mapped[list["ActivitySample"]] = relationship(back_populates="activity", cascade="all, delete-orphan")


class ActivityLap(Base):
    __tablename__ = "activity_laps"

    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id"), index=True)
    lap_index: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_speed_mps: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    activity: Mapped[Activity] = relationship(back_populates="laps")


class ActivitySample(Base):
    __tablename__ = "activity_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id"), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    heart_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    pace_seconds_per_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    cadence: Mapped[float | None] = mapped_column(Float, nullable=True)
    power_watts: Mapped[float | None] = mapped_column(Float, nullable=True)
    altitude_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    speed_mps: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)

    activity: Mapped[Activity] = relationship(back_populates="samples")


class ActivityRelationship(Base):
    __tablename__ = "activity_relationships"

    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id"), index=True)
    related_activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id"), index=True)
    relationship_type: Mapped[str] = mapped_column(String(40), default="possible_duplicate")
    decision_reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DailyRecovery(Base):
    __tablename__ = "daily_recoveries"
    __table_args__ = (UniqueConstraint("user_id", "day", "data_source_id", name="uq_recovery_day_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    data_source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"), nullable=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    recovery_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    hrv_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    resting_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    daily_strain: Mapped[float | None] = mapped_column(Float, nullable=True)
    respiratory_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    skin_temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Sleep(Base):
    __tablename__ = "sleeps"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    data_source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"), nullable=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sleep_duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_need_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    efficiency_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    consistency_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    sleep_debt_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cycles: Mapped[int | None] = mapped_column(Integer, nullable=True)
    respiratory_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    skin_temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class DailySummary(Base):
    __tablename__ = "daily_summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    classification: Mapped[str] = mapped_column(String(40))
    summary_markdown: Mapped[str] = mapped_column(Text)
    data_quality: Mapped[str] = mapped_column(String(80), default="parcial")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SubjectiveCheckin(Base):
    __tablename__ = "subjective_checkins"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_checkin_user_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    perceived_effort: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_quality: Mapped[int | None] = mapped_column(Integer, nullable=True)
    energy: Mapped[int | None] = mapped_column(Integer, nullable=True)
    muscle_soreness: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pain_regions: Mapped[str | None] = mapped_column(Text, nullable=True)
    mood: Mapped[str | None] = mapped_column(String(80), nullable=True)
    caffeine_amount: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_caffeine_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    alcohol: Mapped[str | None] = mapped_column(String(120), nullable=True)
    food_near_sleep: Mapped[str | None] = mapped_column(String(120), nullable=True)
    red_flags: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_name: Mapped[str] = mapped_column(String(80))
    file_name: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(40), default="pending")
    records_imported: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    data_source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40))
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
