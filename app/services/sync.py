import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Activity,
    ActivitySourceLink,
    IntegrationState,
    OAuthCredential,
    SyncLog,
)
from app.services.importers import get_or_create_source
from app.services.reconcile_new import ReconciliationResult, reconcile_strava_activity
from app.services.strava import (
    StravaAuthenticationError,
    StravaClient,
    StravaError,
    StravaRateLimitError,
    decrypt_token,
    encrypt_token,
)
from app.services.streams import persist_strava_streams
from app.services.timezone import parse_datetime, utc_now


def sync_strava(db: Session, user_id: int, client: StravaClient | None = None) -> dict[str, int]:
    source = get_or_create_source(db, "strava", "oauth")
    credential = db.scalar(
        select(OAuthCredential).where(
            OAuthCredential.user_id == user_id, OAuthCredential.data_source_id == source.id
        )
    )
    if not credential:
        raise StravaAuthenticationError("Strava exige conexao.")
    state = db.scalar(
        select(IntegrationState).where(
            IntegrationState.user_id == user_id, IntegrationState.data_source_id == source.id
        )
    )
    if not state:
        state = IntegrationState(user_id=user_id, data_source_id=source.id, status="connected")
        db.add(state)
        db.flush()
    log = SyncLog(
        data_source_id=source.id,
        action="strava_sync",
        status="running",
        message="Sincronizacao iniciada.",
    )
    db.add(log)
    db.flush()
    stats = {"fetched": 0, "created": 0, "linked": 0, "possible_duplicates": 0, "skipped": 0, "errors": 0}
    api = client or StravaClient()
    try:
        token = _fresh_access_token(db, credential, state, api)
        overlap = timedelta(minutes=get_settings().strava_sync_overlap_minutes)
        after = state.last_synced_at - overlap if state.last_synced_at else None
        for payload in api.activities(token, after):
            stats["fetched"] += 1
            try:
                normalized = _normalize_payload(payload)
                result = _sync_activity(db, user_id, source.id, normalized, token, api)
                if result.startswith("created"):
                    stats["created"] += 1
                elif result.startswith("linked"):
                    stats["linked"] += 1
                elif result.startswith("possible_duplicate"):
                    stats["possible_duplicates"] += 1
                else:
                    stats["skipped"] += 1
                if result.endswith("_stream_error"):
                    stats["errors"] += 1
            except (StravaAuthenticationError, StravaRateLimitError):
                raise
            except StravaError:
                stats["errors"] += 1
            except (KeyError, TypeError, ValueError):
                stats["errors"] += 1
        state.last_synced_at = utc_now()
        state.sync_cursor_at = state.last_synced_at
        state.status = "connected"
        state.last_error = None
        state.last_imported_count = stats["created"] + stats["linked"] + stats["possible_duplicates"]
        log.status = "completed"
        log.message = (
            f"fetched={stats['fetched']} created={stats['created']} linked={stats['linked']} "
            f"possible_duplicates={stats['possible_duplicates']} skipped={stats['skipped']} errors={stats['errors']}"
        )
    except StravaAuthenticationError:
        state.status = "reconnect_required"
        state.last_error = "Credencial Strava invalida ou revogada."
        log.status = "failed"
        log.message = state.last_error
        raise
    except StravaRateLimitError:
        state.status = "rate_limited"
        state.last_error = "Limite de requisicoes Strava atingido."
        log.status = "failed"
        log.message = state.last_error
        raise
    except StravaError as exc:
        state.status = "error"
        state.last_error = "Falha temporaria ao sincronizar Strava."
        log.status = "failed"
        log.message = state.last_error
        raise exc
    finally:
        log.finished_at = utc_now()
        db.commit()
    return stats


def _fresh_access_token(
    db: Session, credential: OAuthCredential, state: IntegrationState, client: StravaClient
) -> str:
    access = decrypt_token(credential.encrypted_access_token)
    margin = timedelta(seconds=get_settings().strava_refresh_margin_seconds)
    if credential.expires_at and credential.expires_at > utc_now() + margin:
        return access
    refresh = decrypt_token(credential.encrypted_refresh_token or "")
    response = client.refresh(refresh)
    credential.encrypted_access_token = encrypt_token(response["access_token"])
    if response.get("refresh_token"):
        credential.encrypted_refresh_token = encrypt_token(response["refresh_token"])
    credential.expires_at = datetime.fromtimestamp(response["expires_at"], UTC)
    db.add(credential)
    db.flush()
    return response["access_token"]


def _sync_activity(
    db: Session,
    user_id: int,
    source_id: int,
    payload: dict[str, Any],
    token: str,
    api: StravaClient,
) -> str:
    external_id = str(payload["id"])
    existing_link = db.scalar(
        select(ActivitySourceLink).where(
            ActivitySourceLink.data_source_id == source_id,
            ActivitySourceLink.external_id == external_id,
        )
    )
    if existing_link:
        existing_link.source_data_json = _source_data_json(payload, {"status": "not_requested"})
        db.add(existing_link)
        db.flush()
        return "skipped"

    candidates = _candidate_activities(db, user_id, source_id, payload["started_at"])
    reconciliation = reconcile_strava_activity(payload, candidates)
    if reconciliation.decision == "linked":
        activity = _candidate_by_id(candidates, reconciliation.candidate_activity_id)
        if activity is None:
            raise ValueError("Reconciliacao linked sem candidata valida")
        _fill_missing_aggregates(activity, payload)
        stream_meta = _stream_metadata(token, api, external_id, persist=False)
        _create_source_link(db, activity, source_id, external_id, payload, reconciliation, stream_meta, "linked")
        return "linked_stream_error" if stream_meta.get("status") == "failed" else "linked"

    activity = _import_summary(db, user_id, source_id, payload)
    stream_meta = _stream_metadata(token, api, external_id, persist=True, db=db, activity=activity)
    status = "possible_duplicate" if reconciliation.decision == "possible_duplicate" else "separate"
    _create_source_link(db, activity, source_id, external_id, payload, reconciliation, stream_meta, status)
    base_result = status if status == "possible_duplicate" else "created"
    return f"{base_result}_stream_error" if stream_meta.get("status") == "failed" else base_result


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    external_id = normalized.get("id")
    if external_id is None:
        raise ValueError("Atividade Strava sem id")
    started = parse_datetime(normalized.get("start_date"))
    if not started:
        raise ValueError("Atividade Strava sem start_date")
    normalized["id"] = str(external_id)
    normalized["started_at"] = started
    return normalized


def _candidate_activities(db: Session, user_id: int, source_id: int, started_at: datetime) -> list[Activity]:
    lower = started_at - timedelta(minutes=15)
    upper = started_at + timedelta(minutes=15)
    return list(
        db.scalars(
            select(Activity).where(
                Activity.user_id == user_id,
                Activity.data_source_id != source_id,
                Activity.started_at >= lower,
                Activity.started_at <= upper,
            )
        )
    )


def _candidate_by_id(candidates: list[Activity], candidate_id: int | None) -> Activity | None:
    return next((candidate for candidate in candidates if candidate.id == candidate_id), None)


def _import_summary(db: Session, user_id: int, source_id: int, payload: dict[str, Any]) -> Activity:
    external_id = str(payload["id"])
    existing = db.scalar(
        select(Activity).where(Activity.data_source_id == source_id, Activity.external_id == external_id)
    )
    if existing:
        return existing
    started = parse_datetime(payload.get("start_date"))
    if not started:
        raise ValueError("Atividade Strava sem start_date")
    duration = payload.get("elapsed_time")
    distance = payload.get("distance")
    activity = Activity(
        user_id=user_id,
        data_source_id=source_id,
        external_id=external_id,
        activity_type=str(payload.get("sport_type") or payload.get("type") or "treino").lower(),
        started_at=started,
        total_duration_seconds=duration,
        moving_time_seconds=payload.get("moving_time"),
        distance_meters=distance,
        avg_speed_mps=payload.get("average_speed"),
        avg_pace_seconds_per_km=(duration / (distance / 1000) if duration and distance else None),
        avg_hr=payload.get("average_heartrate"),
        max_hr=payload.get("max_heartrate"),
        cadence=payload.get("average_cadence"),
        power_watts=payload.get("average_watts"),
        elevation_gain_meters=payload.get("total_elevation_gain"),
        notes=payload.get("name"),
    )
    db.add(activity)
    db.flush()
    return activity


def _fill_missing_aggregates(activity: Activity, payload: dict[str, Any]) -> None:
    updates = {
        "moving_time_seconds": payload.get("moving_time"),
        "avg_speed_mps": payload.get("average_speed"),
        "avg_hr": payload.get("average_heartrate"),
        "max_hr": payload.get("max_heartrate"),
        "cadence": payload.get("average_cadence"),
        "power_watts": payload.get("average_watts"),
        "elevation_gain_meters": payload.get("total_elevation_gain"),
    }
    for field, value in updates.items():
        if getattr(activity, field) is None and value is not None:
            setattr(activity, field, value)
    if activity.avg_pace_seconds_per_km is None and activity.total_duration_seconds and activity.distance_meters:
        activity.avg_pace_seconds_per_km = activity.total_duration_seconds / (activity.distance_meters / 1000)


def _create_source_link(
    db: Session,
    activity: Activity,
    source_id: int,
    external_id: str,
    payload: dict[str, Any],
    reconciliation: ReconciliationResult,
    stream_meta: dict[str, Any],
    status: str,
) -> ActivitySourceLink:
    evidence = dict(reconciliation.evidence)
    if reconciliation.candidate_activity_id is not None:
        evidence["candidate_activity_id"] = reconciliation.candidate_activity_id
    link = ActivitySourceLink(
        activity_id=activity.id,
        data_source_id=source_id,
        external_id=external_id,
        source_type="strava",
        source_data_json=_source_data_json(payload, stream_meta),
        status=status,
        reconciliation_method="auto",
        reconciliation_confidence=reconciliation.confidence,
        reconciliation_score=reconciliation.score,
        reconciliation_evidence_json=json.dumps(evidence, sort_keys=True),
        linked_at=utc_now(),
    )
    db.add(link)
    db.flush()
    return link


def _stream_metadata(
    token: str,
    api: StravaClient,
    external_id: str,
    *,
    persist: bool,
    db: Session | None = None,
    activity: Activity | None = None,
) -> dict[str, Any]:
    fetch_streams = getattr(api, "activity_streams", None)
    if fetch_streams is None:
        return {"status": "not_available", "persisted": False, "types": []}
    try:
        streams = fetch_streams(token, external_id)
    except (StravaAuthenticationError, StravaRateLimitError):
        raise
    except StravaError:
        return {
            "status": "failed",
            "persisted": False,
            "types": [],
            "error": "Falha ao buscar streams Strava.",
        }
    stream_types = sorted(key for key in streams if key != "latlng")
    persisted = 0
    if persist:
        if db is None or activity is None:
            raise ValueError("Persistencia de streams sem atividade")
        try:
            persisted = persist_strava_streams(db, activity, streams)
        except (TypeError, ValueError):
            return {
                "status": "failed",
                "persisted": False,
                "types": stream_types,
                "error": "Falha ao persistir streams Strava.",
            }
    return {
        "status": "persisted" if persist else "identified",
        "persisted": persist,
        "samples_created": persisted,
        "types": stream_types,
    }


def _source_data_json(payload: dict[str, Any], stream_meta: dict[str, Any]) -> str:
    safe = {
        "id": payload.get("id"),
        "name": payload.get("name"),
        "type": payload.get("type"),
        "sport_type": payload.get("sport_type"),
        "start_date": payload.get("start_date"),
        "start_date_local": payload.get("start_date_local"),
        "timezone": payload.get("timezone"),
        "elapsed_time": payload.get("elapsed_time"),
        "moving_time": payload.get("moving_time"),
        "distance": payload.get("distance"),
        "average_speed": payload.get("average_speed"),
        "max_speed": payload.get("max_speed"),
        "average_heartrate": payload.get("average_heartrate"),
        "max_heartrate": payload.get("max_heartrate"),
        "average_cadence": payload.get("average_cadence"),
        "average_watts": payload.get("average_watts"),
        "total_elevation_gain": payload.get("total_elevation_gain"),
        "streams": stream_meta,
    }
    return json.dumps(safe, sort_keys=True, default=str)
