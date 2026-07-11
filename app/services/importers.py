import csv
import hashlib
import json
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Activity,
    ActivityLap,
    ActivitySample,
    DailyRecovery,
    DataSource,
    ImportJob,
    Sleep,
    SubjectiveCheckin,
)
from app.services.reconciliation import mark_duplicates_for_review
from app.services.timezone import ensure_utc, parse_datetime


def get_or_create_source(db: Session, name: str, kind: str = "manual") -> DataSource:
    source = db.scalar(select(DataSource).where(DataSource.name == name))
    if source:
        return source
    source = DataSource(name=name, kind=kind)
    db.add(source)
    db.flush()
    return source


def import_file(db: Session, user_id: int, file_path: Path, source_name: str) -> ImportJob:
    suffix = file_path.suffix.lower().lstrip(".")
    job = ImportJob(user_id=user_id, source_name=source_name, file_name=file_path.name, file_type=suffix)
    db.add(job)
    db.flush()
    try:
        if suffix == "csv":
            count = import_csv(db, user_id, file_path, source_name)
        elif suffix == "json":
            count = import_json(db, user_id, file_path, source_name)
        elif suffix == "fit":
            count = import_fit(db, user_id, file_path, source_name)
        else:
            raise ValueError("Formato nao suportado. Use CSV, JSON ou FIT.")
        job.status = "completed"
        job.records_imported = count
    except Exception as exc:
        job.status = "failed"
        job.error_message = str(exc)
    db.commit()
    return job


def import_csv(db: Session, user_id: int, file_path: Path, source_name: str = "manual_csv") -> int:
    source = get_or_create_source(db, source_name, "manual")
    count = 0
    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            activity = _activity_from_payload(row, user_id, source.id, original_file_path=str(file_path))
            db.add(activity)
            db.flush()
            mark_duplicates_for_review(db, activity)
            count += 1
    db.commit()
    return count


def import_json(db: Session, user_id: int, file_path: Path, source_name: str = "manual_json") -> int:
    source = get_or_create_source(db, source_name, "manual")
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    count = 0
    if isinstance(payload, list):
        activities = payload
        payload = {"activities": activities}
    for item in payload.get("activities", []):
        activity = _activity_from_payload(item, user_id, source.id, original_file_path=str(file_path))
        db.add(activity)
        db.flush()
        mark_duplicates_for_review(db, activity)
        count += 1
    for item in payload.get("recovery", []):
        db.add(_recovery_from_payload(item, user_id, source.id))
        count += 1
    for item in payload.get("sleep", []):
        db.add(_sleep_from_payload(item, user_id, source.id))
        count += 1
    for item in payload.get("checkins", []):
        db.add(_checkin_from_payload(item, user_id))
        count += 1
    db.commit()
    return count


def import_fit(db: Session, user_id: int, file_path: Path, source_name: str = "garmin_fit") -> int:
    try:
        from fitparse import FitFile
    except ImportError as exc:
        raise RuntimeError("Biblioteca fitparse nao instalada.") from exc

    source = get_or_create_source(db, source_name, "fit")
    external_id = f"fit:{hashlib.sha256(file_path.read_bytes()).hexdigest()}"
    if db.scalar(select(Activity).where(Activity.data_source_id == source.id, Activity.external_id == external_id)):
        return 0

    fit_file = FitFile(str(file_path))
    sessions: list[dict[str, Any]] = []
    laps: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []

    for message in fit_file.get_messages():
        values = {field.name: field.value for field in message}
        if message.name == "session":
            sessions.append(values)
        elif message.name == "lap":
            laps.append(values)
        elif message.name == "record":
            samples.append(values)

    if not sessions:
        raise ValueError("Arquivo FIT sem mensagem de sessao.")

    session = sessions[0]
    start = parse_datetime(session.get("start_time")) or parse_datetime(session.get("timestamp"))
    if not start:
        raise ValueError("Arquivo FIT sem horario inicial.")
    duration = _to_int(session.get("total_timer_time") or session.get("total_elapsed_time"))
    distance = _to_float(session.get("total_distance"))
    avg_speed = _to_float(session.get("enhanced_avg_speed") or session.get("avg_speed"))
    if avg_speed is None and duration and distance:
        avg_speed = distance / duration
    avg_pace = duration / (distance / 1000) if duration and distance and distance > 0 else None
    device = _fit_device_label(file_path)
    present = sorted(
        {
            key
            for values in (session, *samples)
            for key, value in values.items()
            if value is not None and "position" not in key and not key.endswith(("_lat", "_long"))
        }
    )
    warnings = []
    if not session.get("total_moving_time"):
        warnings.append("Tempo em movimento ausente; usado tempo do cronometro.")
    if not session.get("enhanced_avg_speed") and not session.get("avg_speed"):
        warnings.append("Velocidade media ausente; calculada por distancia e duracao.")
    notes = " | ".join([
        "Origem: FIT Garmin",
        f"Dispositivo: {device}" if device else "Dispositivo: nao identificado",
        f"Qualidade: {'boa' if len(present) >= 8 else 'parcial'}",
        f"Campos presentes: {', '.join(present)}",
        f"Avisos: {'; '.join(warnings) if warnings else 'nenhum'}",
        "Localizacao: ocultada e nao armazenada",
    ])
    activity = Activity(
        user_id=user_id,
        data_source_id=source.id,
        external_id=external_id,
        activity_type=str(session.get("sport") or "treino"),
        started_at=ensure_utc(start),
        ended_at=start + timedelta(seconds=duration) if duration else None,
        total_duration_seconds=duration,
        moving_time_seconds=_to_int(session.get("total_moving_time") or session.get("total_timer_time")),
        distance_meters=distance,
        avg_pace_seconds_per_km=avg_pace,
        avg_speed_mps=avg_speed,
        avg_hr=_to_float(session.get("avg_heart_rate")),
        max_hr=_to_float(session.get("max_heart_rate")),
        cadence=_to_float(session.get("avg_running_cadence") or session.get("avg_cadence")),
        power_watts=_to_float(session.get("avg_power")),
        calories=_to_float(session.get("total_calories")),
        elevation_gain_meters=_to_float(session.get("total_ascent")),
        original_file_path=str(file_path),
        notes=notes,
    )
    db.add(activity)
    db.flush()

    for index, lap in enumerate(laps, start=1):
        db.add(
            ActivityLap(
                activity_id=activity.id,
                lap_index=index,
                started_at=parse_datetime(lap.get("start_time")),
                duration_seconds=_to_float(lap.get("total_timer_time")),
                distance_meters=_to_float(lap.get("total_distance")),
                avg_hr=_to_float(lap.get("avg_heart_rate")),
                max_hr=_to_float(lap.get("max_heart_rate")),
                avg_speed_mps=_to_float(lap.get("enhanced_avg_speed") or lap.get("avg_speed")),
            )
        )

    for sample in samples:
        timestamp = parse_datetime(sample.get("timestamp"))
        if timestamp:
            speed = _to_float(sample.get("enhanced_speed") or sample.get("speed"))
            db.add(
                ActivitySample(
                    activity_id=activity.id,
                    recorded_at=timestamp,
                    heart_rate=_to_float(sample.get("heart_rate")),
                    pace_seconds_per_km=1000 / speed if speed and speed > 0 else None,
                    cadence=_to_float(sample.get("cadence")),
                    power_watts=_to_float(sample.get("power")),
                    altitude_meters=_to_float(sample.get("enhanced_altitude") or sample.get("altitude")),
                    speed_mps=speed,
                    temperature_c=_to_float(sample.get("temperature")),
                )
            )

    mark_duplicates_for_review(db, activity)
    db.commit()
    return 1


def _fit_device_label(file_path: Path) -> str | None:
    from fitparse import FitFile

    for message in FitFile(str(file_path)).get_messages("device_info"):
        values = {field.name: field.value for field in message}
        if values.get("manufacturer") == "garmin" and values.get("garmin_product"):
            label = f"Garmin produto {values['garmin_product']}"
            if values.get("software_version") is not None:
                label += f" (software {values['software_version']})"
            return label
    return None


def save_upload(upload_dir: Path, original_name: str, content: bytes) -> Path:
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(original_name).name
    destination = upload_dir / safe_name
    counter = 1
    while destination.exists():
        destination = upload_dir / f"{Path(safe_name).stem}-{counter}{Path(safe_name).suffix}"
        counter += 1
    destination.write_bytes(content)
    return destination


def copy_demo_file(source: Path, destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    shutil.copyfile(source, destination)
    return destination


def _activity_from_payload(
    payload: dict[str, Any],
    user_id: int,
    source_id: int,
    original_file_path: str | None = None,
) -> Activity:
    start = parse_datetime(payload.get("started_at") or payload.get("start_time") or payload.get("date"))
    if not start:
        raise ValueError("Atividade sem started_at/start_time/date.")
    duration = _to_int(payload.get("total_duration_seconds") or payload.get("duration_seconds") or payload.get("duration"))
    end = parse_datetime(payload.get("ended_at") or payload.get("end_time"))
    if end is None and duration:
        end = start + timedelta(seconds=duration)
    distance = _to_float(payload.get("distance_meters") or payload.get("distance"))
    avg_speed = _to_float(payload.get("avg_speed_mps") or payload.get("avg_speed"))
    avg_pace = _to_float(payload.get("avg_pace_seconds_per_km"))
    if avg_pace is None and distance and duration and distance > 0:
        avg_pace = duration / (distance / 1000)
    return Activity(
        user_id=user_id,
        data_source_id=source_id,
        external_id=_to_str(payload.get("external_id") or payload.get("id")),
        activity_type=str(payload.get("activity_type") or payload.get("type") or "treino").lower(),
        started_at=ensure_utc(start),
        ended_at=end,
        total_duration_seconds=duration,
        moving_time_seconds=_to_int(payload.get("moving_time_seconds") or payload.get("moving_time")),
        distance_meters=distance,
        avg_pace_seconds_per_km=avg_pace,
        avg_speed_mps=avg_speed,
        avg_hr=_to_float(payload.get("avg_hr") or payload.get("average_heartrate")),
        max_hr=_to_float(payload.get("max_hr") or payload.get("max_heartrate")),
        cadence=_to_float(payload.get("cadence")),
        power_watts=_to_float(payload.get("power_watts") or payload.get("average_watts")),
        calories=_to_float(payload.get("calories")),
        elevation_gain_meters=_to_float(payload.get("elevation_gain_meters") or payload.get("total_elevation_gain")),
        strain=_to_float(payload.get("strain")),
        hr_zones_json=json.dumps(payload.get("hr_zones"), ensure_ascii=False) if payload.get("hr_zones") else None,
        original_file_path=original_file_path,
        notes=_to_str(payload.get("notes")),
    )


def _recovery_from_payload(payload: dict[str, Any], user_id: int, source_id: int) -> DailyRecovery:
    return DailyRecovery(
        user_id=user_id,
        data_source_id=source_id,
        day=payload["day"],
        recovery_score=_to_float(payload.get("recovery_score") or payload.get("recovery")),
        hrv_ms=_to_float(payload.get("hrv_ms") or payload.get("hrv")),
        resting_hr=_to_float(payload.get("resting_hr")),
        daily_strain=_to_float(payload.get("daily_strain") or payload.get("strain")),
        respiratory_rate=_to_float(payload.get("respiratory_rate")),
        skin_temperature_c=_to_float(payload.get("skin_temperature_c")),
        notes=_to_str(payload.get("notes")),
    )


def _sleep_from_payload(payload: dict[str, Any], user_id: int, source_id: int) -> Sleep:
    return Sleep(
        user_id=user_id,
        data_source_id=source_id,
        day=payload["day"],
        started_at=parse_datetime(payload.get("started_at")),
        ended_at=parse_datetime(payload.get("ended_at")),
        sleep_duration_seconds=_to_int(payload.get("sleep_duration_seconds")),
        sleep_need_seconds=_to_int(payload.get("sleep_need_seconds")),
        efficiency_percent=_to_float(payload.get("efficiency_percent")),
        consistency_percent=_to_float(payload.get("consistency_percent")),
        sleep_debt_seconds=_to_int(payload.get("sleep_debt_seconds")),
        cycles=_to_int(payload.get("cycles")),
        respiratory_rate=_to_float(payload.get("respiratory_rate")),
        skin_temperature_c=_to_float(payload.get("skin_temperature_c")),
        notes=_to_str(payload.get("notes")),
    )


def _checkin_from_payload(payload: dict[str, Any], user_id: int) -> SubjectiveCheckin:
    return SubjectiveCheckin(
        user_id=user_id,
        day=payload["day"],
        perceived_effort=_to_int(payload.get("perceived_effort")),
        sleep_quality=_to_int(payload.get("sleep_quality")),
        energy=_to_int(payload.get("energy")),
        muscle_soreness=_to_int(payload.get("muscle_soreness")),
        pain_regions=_to_str(payload.get("pain_regions")),
        mood=_to_str(payload.get("mood")),
        caffeine_amount=_to_str(payload.get("caffeine_amount")),
        last_caffeine_at=_to_str(payload.get("last_caffeine_at")),
        alcohol=_to_str(payload.get("alcohol")),
        food_near_sleep=_to_str(payload.get("food_near_sleep")),
        red_flags=_to_str(payload.get("red_flags")),
        notes=_to_str(payload.get("notes")),
    )


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(float(value))


def _to_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
