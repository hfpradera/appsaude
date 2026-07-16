import json
import logging
from datetime import UTC, date, datetime, timedelta
from threading import Thread
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.models import (
    Activity,
    AiMemory,
    DailyRecovery,
    DataSource,
    ImportJob,
    IntegrationState,
    OAuthCredential,
    Sleep,
    SubjectiveCheckin,
    SyncLog,
    User,
)
from app.security import (
    make_oauth_state_cookie,
    make_session_cookie,
    password_matches,
    verify_oauth_state_cookie,
    verify_session_cookie,
)
from app.services import reports
from app.services.ai_analysis import ai_status, generate_daily_analysis, latest_daily_analysis
from app.services.ai_chat import (
    archive_conversation,
    cancel_run,
    conversation_payload,
    create_conversation,
    get_conversation,
    list_conversations,
    send_message,
    stream_run,
)
from app.services.ai_tool_executor import cancel_pending_action, confirm_pending_action
from app.services.ai_tools import (
    ToolError,
    associate_shoe_with_activity,
    create_meal_log,
    create_shoe,
    delete_meal_log,
    delete_memory,
    get_meal_history,
    get_shoe_details,
    get_shoe_usage_history,
    get_shoes,
    retire_shoe,
    save_confirmed_memory,
    update_meal_log,
    update_memory,
    update_shoe,
)
from app.services.dashboard_api import (
    activities_csv,
    activities_payload,
    build_filters,
    calendar_payload,
    dashboard_payload,
    data_quality_payload,
    metrics_payload,
    summary_payload,
    timeline_payload,
    trend_payload,
)
from app.services.importers import import_file, save_upload
from app.services.strava import (
    StravaClient,
    StravaError,
    authorization_url,
    encrypt_token,
    make_oauth_state,
    scopes_are_sufficient,
)
from app.services import sync_locks
from app.services.sync import sync_strava
from app.services.timezone import seconds_to_human, to_local
from app.services.whoop import (
    WHOOP_SCOPES,
    WhoopClient,
    WhoopError,
    fresh_access_token,
)
from app.services.whoop import (
    authorization_url as whoop_authorization_url,
)
from app.services.whoop_sync import sync_whoop

router = APIRouter()
logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["seconds"] = seconds_to_human
templates.env.filters["localdt"] = lambda value: (
    to_local(value).strftime("%d/%m/%Y %H:%M") if value else "-"
)

def render(
    request: Request,
    name: str,
    context: dict[str, object],
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = verify_session_cookie(request.cookies.get("hp_session"))
    if not user_id:
        raise NotAuthenticated()
    user = db.get(User, user_id)
    if not user:
        raise NotAuthenticated()
    return user


class NotAuthenticated(Exception):
    pass


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return render(request, "login.html", {"request": request, "error": None})


@router.post("/login")
def login(request: Request, password: str = Form(...), db: Session = Depends(get_db)) -> Response:
    if not password_matches(password):
        return render(
            request, "login.html", {"request": request, "error": "Senha invalida"}, status_code=401
        )
    user = db.scalar(select(User).limit(1))
    if not user:
        user = User(name="Humberto", timezone=get_settings().app_timezone)
        db.add(user)
        db.commit()
        db.refresh(user)
    response = RedirectResponse("/hoje", status_code=303)
    response.set_cookie("hp_session", make_session_cookie(user.id), httponly=True, samesite="lax")
    return response


@router.post("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("hp_session")
    return response


@router.get("/")
def home() -> RedirectResponse:
    return RedirectResponse("/hoje", status_code=303)


@router.get("/hoje", response_class=HTMLResponse)
def today_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> HTMLResponse:
    data = reports.dashboard(db, user.id, date.today())
    analysis = latest_daily_analysis(db, user.id, date.today())
    return render(
        request,
        "dashboard.html",
        {"request": request, "user": user, "data": data, "analysis": analysis},
    )


@router.get("/api/dashboard/summary")
def dashboard_summary(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, object]:
    filters = _dashboard_filters_from_request(request)
    return summary_payload(db, user.id, filters)


@router.get("/api/dashboard/metrics")
def dashboard_metrics(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, object]:
    filters = _dashboard_filters_from_request(request)
    return {"filters": filters.__dict__, "metrics": metrics_payload(db, user.id, filters), "trend": trend_payload(db, user.id, filters)}


@router.get("/api/dashboard/timeline")
def dashboard_timeline(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, object]:
    filters = _dashboard_filters_from_request(request)
    return {"filters": filters.__dict__, "timeline": timeline_payload(db, user.id, filters)}


@router.get("/api/dashboard/activities")
def dashboard_activities(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, object]:
    filters = _dashboard_filters_from_request(request)
    return {"filters": filters.__dict__, "activities": activities_payload(db, user.id, filters)}


@router.get("/api/dashboard/activities.csv")
def dashboard_activities_csv(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> Response:
    filters = _dashboard_filters_from_request(request)
    return Response(
        activities_csv(db, user.id, filters),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=atividades-dashboard.csv"},
    )


@router.get("/api/dashboard/calendar")
def dashboard_calendar(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, object]:
    filters = _dashboard_filters_from_request(request)
    return calendar_payload(db, user.id, filters.day)


@router.get("/api/dashboard/data-quality")
def dashboard_data_quality(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, object]:
    filters = _dashboard_filters_from_request(request)
    return data_quality_payload(db, user.id, filters)


@router.get("/api/dashboard")
def dashboard_api(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, object]:
    filters = _dashboard_filters_from_request(request)
    return dashboard_payload(db, user.id, filters)


@router.post("/api/dashboard/analysis/generate")
def dashboard_analysis_generate(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, object]:
    filters = _dashboard_filters_from_request(request)
    summary = generate_daily_analysis(db, user.id, filters.day)
    return {
        "day": summary.day.isoformat(),
        "created_at": to_local(summary.created_at).isoformat(),
        "message": "Analise diaria gerada",
    }


def _dashboard_filters_from_request(request: Request):
    params = request.query_params
    day = _parse_date(params.get("date")) or date.today()
    return build_filters(
        day=day,
        period=params.get("period", "today"),
        start_date=_parse_date(params.get("start_date")),
        end_date=_parse_date(params.get("end_date")),
        source=params.get("source", "all"),
        activity_type=params.get("activity_type", "all"),
        status=params.get("status", "all"),
        search=params.get("search", ""),
        page=_parse_int(params.get("page"), 1),
        page_size=_parse_int(params.get("page_size"), 25),
    )


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_int(value: str | None, default: int) -> int:
    try:
        return int(value or default)
    except ValueError:
        return default


@router.get("/atividades", response_class=HTMLResponse)
def activities_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> HTMLResponse:
    activities = db.scalars(
        select(Activity).where(Activity.user_id == user.id).order_by(Activity.started_at.desc())
    ).all()
    return render(
        request, "activities.html", {"request": request, "user": user, "activities": activities}
    )


@router.get("/atividades/{activity_id}", response_class=HTMLResponse)
def activity_detail(
    activity_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> HTMLResponse:
    activity = db.get(Activity, activity_id)
    if not activity or activity.user_id != user.id:
        return render(
            request, "not_found.html", {"request": request, "user": user}, status_code=404
        )
    return render(
        request, "activity_detail.html", {"request": request, "user": user, "activity": activity}
    )


@router.get("/sono", response_class=HTMLResponse)
def sleep_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> HTMLResponse:
    sleeps = db.scalars(
        select(Sleep).where(Sleep.user_id == user.id).order_by(Sleep.day.desc())
    ).all()
    return render(request, "sleep.html", {"request": request, "user": user, "sleeps": sleeps})


@router.get("/recuperacao", response_class=HTMLResponse)
def recovery_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> HTMLResponse:
    recoveries = db.scalars(
        select(DailyRecovery)
        .where(DailyRecovery.user_id == user.id)
        .order_by(DailyRecovery.day.desc())
    ).all()
    return render(
        request, "recovery.html", {"request": request, "user": user, "recoveries": recoveries}
    )


@router.get("/checkin", response_class=HTMLResponse)
def checkin_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> HTMLResponse:
    checkin = reports.latest_checkin(db, user.id, date.today())
    return render(request, "checkin.html", {"request": request, "user": user, "checkin": checkin})


@router.post("/checkin")
def save_checkin(
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    perceived_effort: int = Form(0),
    sleep_quality: int = Form(0),
    energy: int = Form(0),
    muscle_soreness: int = Form(0),
    pain_regions: str = Form(""),
    mood: str = Form(""),
    caffeine_amount: str = Form(""),
    last_caffeine_at: str = Form(""),
    alcohol: str = Form(""),
    food_near_sleep: str = Form(""),
    red_flags: str = Form(""),
    notes: str = Form(""),
) -> RedirectResponse:
    checkin = reports.latest_checkin(db, user.id, date.today()) or SubjectiveCheckin(
        user_id=user.id, day=date.today()
    )
    checkin.perceived_effort = perceived_effort
    checkin.sleep_quality = sleep_quality
    checkin.energy = energy
    checkin.muscle_soreness = muscle_soreness
    checkin.pain_regions = pain_regions
    checkin.mood = mood
    checkin.caffeine_amount = caffeine_amount
    checkin.last_caffeine_at = last_caffeine_at
    checkin.alcohol = alcohol
    checkin.food_near_sleep = food_near_sleep
    checkin.red_flags = red_flags
    checkin.notes = notes
    db.add(checkin)
    db.commit()
    return RedirectResponse("/hoje", status_code=303)


@router.get("/relatorio-semanal", response_class=HTMLResponse)
def weekly_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> HTMLResponse:
    report = reports.weekly_report(db, user.id, date.today())
    daily = reports.daily_report_markdown(db, user.id, date.today())
    return render(
        request,
        "weekly_report.html",
        {"request": request, "user": user, "report": report, "daily": daily},
    )


@router.get("/importacoes", response_class=HTMLResponse)
def imports_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> HTMLResponse:
    jobs = db.scalars(
        select(ImportJob).where(ImportJob.user_id == user.id).order_by(ImportJob.created_at.desc())
    ).all()
    return render(request, "imports.html", {"request": request, "user": user, "jobs": jobs})


@router.post("/importacoes")
async def upload_import(
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    source_name: str = Form("manual"),
    file: UploadFile = File(...),
) -> RedirectResponse:
    content = await file.read()
    destination = save_upload(get_settings().upload_dir, file.filename or "upload", content)
    import_file(db, user.id, destination, source_name)
    return RedirectResponse("/importacoes", status_code=303)


@router.get("/integracoes", response_class=HTMLResponse)
def integrations_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> HTMLResponse:
    settings = get_settings()
    strava_missing = []
    if not settings.strava_client_id:
        strava_missing.append("Client ID")
    if not settings.strava_client_secret:
        strava_missing.append("Client Secret")
    if not settings.token_encryption_key:
        strava_missing.append("TOKEN_ENCRYPTION_KEY")
    strava_source = db.scalar(select(DataSource).where(DataSource.name == "strava"))
    credential = None
    integration_state = None
    if strava_source:
        credential = db.scalar(
            select(OAuthCredential).where(
                OAuthCredential.user_id == user.id,
                OAuthCredential.data_source_id == strava_source.id,
            )
        )
        integration_state = db.scalar(
            select(IntegrationState).where(
                IntegrationState.user_id == user.id,
                IntegrationState.data_source_id == strava_source.id,
            )
        )
        last_sync_log = db.scalar(
            select(SyncLog)
            .where(SyncLog.data_source_id == strava_source.id, SyncLog.action == "strava_sync")
            .order_by(SyncLog.started_at.desc(), SyncLog.id.desc())
            .limit(1)
        )
    else:
        last_sync_log = None
    connected = credential is not None
    sync_progress = _sync_progress(last_sync_log)
    sync_running = bool(
        last_sync_log and last_sync_log.status == "running" and last_sync_log.finished_at is None
    )
    whoop_context = _whoop_context(db, user)
    return render(
        request,
        "integrations.html",
        {
            "request": request,
            "user": user,
            "strava_status": "conectado"
            if connected
            else "desativado"
            if not settings.strava_enabled
            else "nao configurado"
            if not settings.strava_configured
            else "pronto para conectar",
            "strava_connected": connected,
            "strava_configured": settings.strava_configured,
            "strava_scopes": credential.scopes if credential else None,
            "strava_last_sync": integration_state.last_synced_at if integration_state else None,
            "strava_last_sync_log": last_sync_log,
            "strava_last_summary": _sync_summary(last_sync_log),
            "strava_sync_progress": sync_progress,
            "strava_sync_running": sync_running or sync_locks.is_running("strava"),
            "strava_missing": strava_missing,
            "strava_redirect_uri": settings.effective_strava_redirect_uri,
            "message": request.query_params.get("message"),
            **whoop_context,
        },
    )


def _whoop_context(db: Session, user: User) -> dict[str, object]:
    settings = get_settings()
    missing = []
    if not settings.whoop_client_id:
        missing.append("Client ID")
    if not settings.whoop_client_secret:
        missing.append("Client Secret")
    if not settings.token_encryption_key:
        missing.append("TOKEN_ENCRYPTION_KEY")
    source = db.scalar(select(DataSource).where(DataSource.name == "whoop"))
    credential = None
    state = None
    last_sync_log = None
    if source:
        credential, state = _integration_credentials(db, user.id, source.id)
        last_sync_log = db.scalar(
            select(SyncLog)
            .where(SyncLog.data_source_id == source.id, SyncLog.action == "whoop_sync")
            .order_by(SyncLog.started_at.desc(), SyncLog.id.desc())
            .limit(1)
        )
    connected = bool(credential and (not state or state.status == "connected"))
    running = bool(
        last_sync_log and last_sync_log.status == "running" and last_sync_log.finished_at is None
    )
    return {
        "whoop_status": "conectado"
        if connected
        else "desativado"
        if not settings.whoop_enabled
        else "nao configurado"
        if not settings.whoop_configured
        else "precisa reconectar"
        if state and state.status == "needs_reconnect"
        else "pronto para conectar",
        "whoop_connected": connected,
        "whoop_configured": settings.whoop_configured,
        "whoop_scopes": credential.scopes if credential else None,
        "whoop_last_sync": state.last_synced_at if state else None,
        "whoop_last_sync_log": last_sync_log,
        "whoop_last_summary": _sync_summary(last_sync_log),
        "whoop_sync_progress": _sync_progress(last_sync_log),
        "whoop_sync_running": running or sync_locks.is_running("whoop"),
        "whoop_missing": missing,
        "whoop_redirect_uri": settings.effective_whoop_redirect_uri,
        "whoop_athlete": state.athlete_name if state else None,
        "whoop_last_error": state.last_error if state else None,
    }


def _integration_credentials(
    db: Session, user_id: int, source_id: int
) -> tuple[OAuthCredential | None, IntegrationState | None]:
    credential = db.scalar(
        select(OAuthCredential).where(
            OAuthCredential.user_id == user_id,
            OAuthCredential.data_source_id == source_id,
        )
    )
    state = db.scalar(
        select(IntegrationState).where(
            IntegrationState.user_id == user_id,
            IntegrationState.data_source_id == source_id,
        )
    )
    return credential, state


@router.get("/integrations/strava/connect")
def strava_connect(user: User = Depends(current_user)) -> Response:
    state = make_oauth_state()
    try:
        response = RedirectResponse(authorization_url(state), status_code=303)
    except RuntimeError:
        return RedirectResponse("/integracoes?message=Strava+nao+configurado", status_code=303)
    response.set_cookie(
        "hp_strava_state",
        make_oauth_state_cookie(state),
        httponly=True,
        samesite="lax",
        max_age=600,
    )
    return response


@router.get("/integrations/strava/callback")
def strava_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    scope: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Response:
    if error:
        return RedirectResponse("/integracoes?message=Autorizacao+Strava+recusada", status_code=303)
    if (
        not code
        or not state
        or not verify_oauth_state_cookie(request.cookies.get("hp_strava_state"), state)
    ):
        return RedirectResponse("/integracoes?message=Retorno+OAuth+invalido", status_code=303)
    try:
        tokens = StravaClient().exchange_code(code)
        granted_scope = tokens.get("scope") or scope
        if not scopes_are_sufficient(granted_scope):
            raise StravaError("Escopo activity:read nao concedido.")
        source = db.scalar(select(DataSource).where(DataSource.name == "strava")) or DataSource(
            name="strava", kind="oauth"
        )
        db.add(source)
        db.flush()
        credential = db.scalar(
            select(OAuthCredential).where(
                OAuthCredential.user_id == user.id, OAuthCredential.data_source_id == source.id
            )
        )
        if credential is None:
            credential = OAuthCredential(
                user_id=user.id, data_source_id=source.id, encrypted_access_token=""
            )
        credential.encrypted_access_token = encrypt_token(tokens["access_token"])
        credential.encrypted_refresh_token = encrypt_token(tokens["refresh_token"])
        if tokens.get("expires_at"):
            credential.expires_at = datetime.fromtimestamp(int(tokens["expires_at"]), UTC)
        credential.token_type = tokens.get("token_type")
        credential.scopes = granted_scope
        db.add(credential)
        integration_state = db.scalar(
            select(IntegrationState).where(
                IntegrationState.user_id == user.id,
                IntegrationState.data_source_id == source.id,
            )
        )
        if integration_state is None:
            integration_state = IntegrationState(user_id=user.id, data_source_id=source.id)
        athlete = tokens.get("athlete") or {}
        integration_state.status = "connected"
        integration_state.athlete_external_id = str(athlete.get("id")) if athlete.get("id") else None
        full_name = " ".join(
            part for part in [athlete.get("firstname"), athlete.get("lastname")] if part
        )
        integration_state.athlete_name = full_name or athlete.get("username")
        integration_state.last_error = None
        db.add(integration_state)
        db.commit()
    except (RuntimeError, StravaError, KeyError, ValueError):
        db.rollback()
        return RedirectResponse("/integracoes?message=Falha+ao+conectar+Strava", status_code=303)
    response = RedirectResponse("/integracoes?message=Strava+conectado", status_code=303)
    response.delete_cookie("hp_strava_state")
    return response


@router.post("/integrations/strava/disconnect")
def strava_disconnect(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> RedirectResponse:
    source = db.scalar(select(DataSource).where(DataSource.name == "strava"))
    if source:
        credentials = db.scalars(
            select(OAuthCredential).where(
                OAuthCredential.user_id == user.id,
                OAuthCredential.data_source_id == source.id,
            )
        ).all()
        for credential in credentials:
            db.delete(credential)
        db.add(
            SyncLog(
                data_source_id=source.id,
                action="strava_disconnect",
                status="completed",
                message="Credenciais locais removidas.",
            )
        )
        db.commit()
    return RedirectResponse("/integracoes?message=Strava+desconectado", status_code=303)


@router.post("/integrations/strava/sync")
def strava_sync(
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> RedirectResponse:
    source = db.scalar(select(DataSource).where(DataSource.name == "strava"))
    if source is None:
        return RedirectResponse("/integracoes?message=Strava+nao+conectado", status_code=303)
    credential = db.scalar(
        select(OAuthCredential).where(
            OAuthCredential.user_id == user.id,
            OAuthCredential.data_source_id == source.id,
        )
    )
    if credential is None:
        return RedirectResponse("/integracoes?message=Strava+nao+conectado", status_code=303)
    if not sync_locks.try_start("strava"):
        return RedirectResponse("/integracoes?message=Sincronizacao+ja+em+andamento", status_code=303)
    Thread(target=_run_strava_sync_background, args=(user.id,), daemon=True).start()
    return RedirectResponse("/integracoes?message=Sincronizacao+iniciada", status_code=303)


@router.get("/integrations/whoop/connect")
def whoop_connect(user: User = Depends(current_user)) -> Response:
    state = make_oauth_state()
    try:
        response = RedirectResponse(whoop_authorization_url(state), status_code=303)
    except RuntimeError:
        return RedirectResponse("/integracoes?message=WHOOP+nao+configurado", status_code=303)
    response.set_cookie(
        "hp_whoop_state",
        make_oauth_state_cookie(state),
        httponly=True,
        samesite="lax",
        max_age=600,
    )
    return response


@router.get("/integrations/whoop/callback")
def whoop_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Response:
    if error:
        return RedirectResponse("/integracoes?message=Autorizacao+WHOOP+recusada", status_code=303)
    if (
        not code
        or not state
        or not verify_oauth_state_cookie(request.cookies.get("hp_whoop_state"), state)
    ):
        return RedirectResponse("/integracoes?message=Retorno+OAuth+WHOOP+invalido", status_code=303)
    try:
        tokens = WhoopClient().exchange_code(code)
        source = db.scalar(select(DataSource).where(DataSource.name == "whoop")) or DataSource(
            name="whoop", kind="oauth"
        )
        db.add(source)
        db.flush()
        credential, integration_state = _integration_credentials(db, user.id, source.id)
        if credential is None:
            credential = OAuthCredential(
                user_id=user.id, data_source_id=source.id, encrypted_access_token=""
            )
        credential.encrypted_access_token = encrypt_token(tokens["access_token"])
        if tokens.get("refresh_token"):
            credential.encrypted_refresh_token = encrypt_token(tokens["refresh_token"])
        if tokens.get("expires_at"):
            credential.expires_at = datetime.fromtimestamp(int(tokens["expires_at"]), UTC)
        elif tokens.get("expires_in"):
            credential.expires_at = datetime.now(UTC) + timedelta(seconds=int(tokens["expires_in"]))
        credential.token_type = tokens.get("token_type")
        credential.scopes = tokens.get("scope") or WHOOP_SCOPES
        db.add(credential)
        if integration_state is None:
            integration_state = IntegrationState(user_id=user.id, data_source_id=source.id)
        integration_state.status = "connected"
        integration_state.last_error = None
        db.add(integration_state)
        db.flush()
        try:
            access_token = fresh_access_token(db, credential, integration_state, WhoopClient())
            profile = WhoopClient().profile(access_token)
            integration_state.athlete_external_id = (
                str(profile.get("user_id")) if profile.get("user_id") else None
            )
            full_name = " ".join(
                part for part in [profile.get("first_name"), profile.get("last_name")] if part
            )
            integration_state.athlete_name = full_name or profile.get("email")
            db.add(integration_state)
        except WhoopError:
            integration_state.athlete_name = "WHOOP conectado"
            db.add(integration_state)
        db.commit()
    except (RuntimeError, WhoopError, KeyError, ValueError):
        db.rollback()
        return RedirectResponse("/integracoes?message=Falha+ao+conectar+WHOOP", status_code=303)
    response = RedirectResponse("/integracoes?message=WHOOP+conectado", status_code=303)
    response.delete_cookie("hp_whoop_state")
    return response


@router.post("/integrations/whoop/disconnect")
def whoop_disconnect(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> RedirectResponse:
    source = db.scalar(select(DataSource).where(DataSource.name == "whoop"))
    if source:
        credentials = db.scalars(
            select(OAuthCredential).where(
                OAuthCredential.user_id == user.id,
                OAuthCredential.data_source_id == source.id,
            )
        ).all()
        for credential in credentials:
            db.delete(credential)
        integration_state = db.scalar(
            select(IntegrationState).where(
                IntegrationState.user_id == user.id,
                IntegrationState.data_source_id == source.id,
            )
        )
        if integration_state:
            integration_state.status = "disconnected"
            integration_state.last_error = None
            db.add(integration_state)
        db.add(
            SyncLog(
                data_source_id=source.id,
                action="whoop_disconnect",
                status="completed",
                message="Credenciais locais removidas.",
            )
        )
        db.commit()
    return RedirectResponse("/integracoes?message=WHOOP+desconectado", status_code=303)


@router.post("/integrations/whoop/sync")
def whoop_sync(
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> RedirectResponse:
    source = db.scalar(select(DataSource).where(DataSource.name == "whoop"))
    if source is None:
        return RedirectResponse("/integracoes?message=WHOOP+nao+conectado", status_code=303)
    credential = db.scalar(
        select(OAuthCredential).where(
            OAuthCredential.user_id == user.id,
            OAuthCredential.data_source_id == source.id,
        )
    )
    if credential is None:
        return RedirectResponse("/integracoes?message=WHOOP+nao+conectado", status_code=303)
    if not sync_locks.try_start("whoop"):
        return RedirectResponse("/integracoes?message=Sincronizacao+WHOOP+ja+em+andamento", status_code=303)
    Thread(target=_run_whoop_sync_background, args=(user.id,), daemon=True).start()
    return RedirectResponse("/integracoes?message=Sincronizacao+WHOOP+iniciada", status_code=303)


@router.post("/integrations/sync-all")
def sync_all(user: User = Depends(current_user)) -> RedirectResponse:
    started = []
    if sync_locks.try_start("strava"):
        Thread(target=_run_strava_sync_background, args=(user.id,), daemon=True).start()
        started.append("strava")
    if sync_locks.try_start("whoop"):
        Thread(target=_run_whoop_sync_background, args=(user.id,), daemon=True).start()
        started.append("whoop")
    if not started:
        return RedirectResponse("/integracoes?message=Sincronizacao+ja+em+andamento", status_code=303)
    return RedirectResponse("/integracoes?message=Sincronizacao+geral+iniciada", status_code=303)


def _run_strava_sync_background(user_id: int) -> None:
    with SessionLocal() as db:
        try:
            sync_strava(db, user_id)
        except Exception:
            logger.exception("Sincronizacao Strava em segundo plano falhou.")
        finally:
            sync_locks.finish("strava")


def _run_whoop_sync_background(user_id: int) -> None:
    with SessionLocal() as db:
        try:
            sync_whoop(db, user_id)
        except Exception:
            logger.exception("Sincronizacao WHOOP em segundo plano falhou.")
        finally:
            sync_locks.finish("whoop")


def _sync_progress(log: SyncLog | None) -> dict[str, int | str] | None:
    if not log or not log.message:
        return None
    if log.status != "running":
        return None
    try:
        data = json.loads(log.message)
    except json.JSONDecodeError:
        return {"status": log.status, "text": log.message}
    processed = int(data.get("processed") or data.get("fetched") or 0)
    limit = int(data.get("limit") or processed or 1)
    percent = min(100, int((processed / limit) * 100)) if limit else 0
    data["percent"] = percent
    data["processed"] = processed
    data["limit"] = limit
    return data


def _sync_summary(log: SyncLog | None) -> dict[str, object] | None:
    if not log:
        return None
    summary: dict[str, object] = {
        "status": _human_sync_status(log.status),
        "is_error": log.status in {"failed", "auth_failed", "rate_limited"},
        "lines": [],
        "error": None,
    }
    if not log.message:
        return summary
    try:
        data = json.loads(log.message)
    except json.JSONDecodeError:
        summary["error"] = log.message if log.status != "completed" else None
        return summary

    items: list[str] = []
    for label, key in [
        ("importados", "created"),
        ("atualizados", "updated"),
        ("ignorados", "skipped"),
        ("lidos", "fetched"),
        ("processados", "processed"),
        ("recovery", "recovery"),
        ("sono", "sleep"),
        ("ciclos", "cycles"),
        ("treinos", "workouts"),
        ("medidas corporais", "body_measurements"),
        ("erros", "errors"),
    ]:
        value = data.get(key)
        if isinstance(value, int) and value:
            items.append(f"{label}: {value}")
    summary["lines"] = items
    if log.status != "completed":
        summary["error"] = data.get("error")
    return summary


def _human_sync_status(status: str) -> str:
    return {
        "completed": "Concluida",
        "running": "Em andamento",
        "failed": "Falhou",
        "auth_failed": "Precisa reconectar",
        "rate_limited": "Limite temporario da API",
    }.get(status, status)


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request) -> HTMLResponse:
    return render(request, "privacy.html", {"request": request})


@router.get("/analise-ia", response_class=HTMLResponse)
def ai_analysis_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> HTMLResponse:
    summary = latest_daily_analysis(db, user.id, date.today())
    data = reports.dashboard(db, user.id, date.today())
    return render(
        request,
        "ai_analysis.html",
        {
            "request": request,
            "user": user,
            "summary": summary,
            "data": data,
            "ai": ai_status(),
            "message": request.query_params.get("message"),
        },
    )


@router.post("/analise-ia/gerar")
def generate_ai_analysis(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> RedirectResponse:
    generate_daily_analysis(db, user.id, date.today())
    return RedirectResponse("/analise-ia?message=Analise+diaria+gerada", status_code=303)


@router.get("/assistente", response_class=HTMLResponse)
def assistant_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> HTMLResponse:
    conversations = [conversation_payload(db, item) for item in list_conversations(db, user.id)]
    return render(
        request,
        "assistant.html",
        {"request": request, "user": user, "conversations": conversations},
    )


@router.get("/tenis", response_class=HTMLResponse)
def shoes_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> HTMLResponse:
    shoes = get_shoes(db, user.id).data["shoes"]
    return render(request, "shoes.html", {"request": request, "user": user, "shoes": shoes})


@router.get("/configuracoes/memoria", response_class=HTMLResponse)
def ai_memory_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> HTMLResponse:
    memories = db.scalars(
        select(AiMemory).where(AiMemory.user_id == user.id).order_by(AiMemory.updated_at.desc())
    ).all()
    return render(
        request,
        "ai_memory.html",
        {"request": request, "user": user, "memories": memories},
    )


@router.get("/api/ai/conversations")
def api_ai_conversations(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, object]:
    return {"conversations": [conversation_payload(db, item) for item in list_conversations(db, user.id)]}


@router.post("/api/ai/conversations")
async def api_create_ai_conversation(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, object]:
    payload = await request.json()
    conversation = create_conversation(db, user.id, payload.get("title"))
    return {"conversation": conversation_payload(db, conversation, include_messages=True)}


@router.get("/api/ai/conversations/{conversation_id}")
def api_get_ai_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, object]:
    conversation = get_conversation(db, user.id, conversation_id)
    return {"conversation": conversation_payload(db, conversation, include_messages=True)}


@router.delete("/api/ai/conversations/{conversation_id}")
def api_delete_ai_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, object]:
    archive_conversation(db, user.id, conversation_id)
    return {"ok": True}


@router.post("/api/ai/conversations/{conversation_id}/messages")
async def api_send_ai_message(
    conversation_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, object]:
    payload = await request.json()
    content = str(payload.get("content") or "")
    return send_message(db, user.id, conversation_id, content)


@router.post("/api/ai/runs/{run_id}/cancel")
def api_cancel_ai_run(
    run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, object]:
    return {"ok": True, "run": cancel_run(db, user.id, run_id)}


@router.get("/api/ai/runs/{run_id}/stream")
def api_stream_ai_run(
    run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> StreamingResponse:
    return StreamingResponse(
        stream_run(db, user.id, run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/api/ai/pending-actions/{pending_id}/confirm")
def api_confirm_pending_action(
    pending_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, object]:
    return confirm_pending_action(db, user.id, pending_id)


@router.post("/api/ai/pending-actions/{pending_id}/cancel")
def api_cancel_pending_action(
    pending_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, object]:
    return cancel_pending_action(db, user.id, pending_id)


@router.get("/api/ai/memories")
def api_ai_memories(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, object]:
    memories = db.scalars(
        select(AiMemory)
        .where(AiMemory.user_id == user.id, AiMemory.active.is_(True))
        .order_by(AiMemory.updated_at.desc())
    ).all()
    return {
        "memories": [
            {
                "id": memory.id,
                "category": memory.category,
                "key": memory.key,
                "value_json": memory.value_json,
                "confirmed_by_user": memory.confirmed_by_user,
                "active": memory.active,
            }
            for memory in memories
        ]
    }


@router.post("/api/ai/memories")
async def api_create_ai_memory(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, object]:
    payload = await request.json()
    result = save_confirmed_memory(
        db,
        user.id,
        str(payload.get("category") or ""),
        str(payload.get("key") or ""),
        payload.get("value"),
    )
    return result.data


@router.patch("/api/ai/memories/{memory_id}")
async def api_update_ai_memory(
    memory_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, object]:
    payload = await request.json()
    return update_memory(db, user.id, memory_id, payload.get("value")).data


@router.delete("/api/ai/memories/{memory_id}")
def api_delete_ai_memory(
    memory_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, object]:
    return delete_memory(db, user.id, memory_id).data


@router.get("/api/meals")
def api_meals(
    start_date: str | None = None,
    end_date: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, object]:
    start = _parse_date(start_date) or date.today()
    end = _parse_date(end_date) or start
    return get_meal_history(db, user.id, start, end).data


@router.post("/api/meals")
async def api_create_meal(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, object]:
    payload = await request.json()
    try:
        consumed_at = datetime.fromisoformat(payload["consumed_at"]) if payload.get("consumed_at") else None
        return create_meal_log(
            db,
            user.id,
            description=str(payload.get("description") or ""),
            consumed_at=consumed_at,
            meal_type=str(payload.get("meal_type") or "refeicao"),
            items=payload.get("items") or [],
            source="manual",
        ).data
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}


@router.patch("/api/meals/{meal_id}")
async def api_update_meal(
    meal_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, object]:
    payload = await request.json()
    return update_meal_log(db, user.id, meal_id, **payload).data


@router.delete("/api/meals/{meal_id}")
def api_delete_meal(
    meal_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, object]:
    return delete_meal_log(db, user.id, meal_id).data


@router.get("/api/shoes")
def api_shoes(
    status: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, object]:
    return get_shoes(db, user.id, status=status).data


@router.post("/api/shoes")
async def api_create_shoe(
    request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, object]:
    payload = await request.json()
    attrs = dict(payload)
    name = str(attrs.pop("name", "") or "")
    return create_shoe(db, user.id, name, **attrs).data


@router.get("/api/shoes/{shoe_id}")
def api_shoe_detail(
    shoe_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, object]:
    return get_shoe_details(db, user.id, shoe_id).data


@router.patch("/api/shoes/{shoe_id}")
async def api_update_shoe(
    shoe_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, object]:
    payload = await request.json()
    return update_shoe(db, user.id, shoe_id, **payload).data


@router.post("/api/shoes/{shoe_id}/retire")
async def api_retire_shoe(
    shoe_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, object]:
    payload = await request.json()
    return retire_shoe(db, user.id, shoe_id, payload.get("notes")).data


@router.get("/api/shoes/{shoe_id}/usages")
def api_shoe_usages(
    shoe_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict[str, object]:
    return get_shoe_usage_history(db, user.id, shoe_id).data


@router.post("/api/shoes/{shoe_id}/associate-activity")
async def api_associate_shoe(
    shoe_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict[str, object]:
    payload = await request.json()
    return associate_shoe_with_activity(db, user.id, shoe_id, int(payload["activity_id"])).data


def _mask_database_url(url: str) -> str:
    if url.startswith("sqlite"):
        return url
    parts = urlsplit(url)
    host = parts.hostname or "?"
    port = f":{parts.port}" if parts.port else ""
    db_name = parts.path.lstrip("/") or "?"
    return f"{parts.scheme}://{host}{port}/{db_name}"


@router.get("/configuracoes", response_class=HTMLResponse)
def settings_page(request: Request, user: User = Depends(current_user)) -> HTMLResponse:
    settings = get_settings()
    return render(
        request,
        "settings.html",
        {
            "request": request,
            "user": user,
            "settings": settings,
            "database_summary": _mask_database_url(settings.database_url),
        },
    )


@router.get("/export/json")
def export_json(db: Session = Depends(get_db), user: User = Depends(current_user)) -> Response:
    return Response(reports.export_json(db, user.id, date.today()), media_type="application/json")


@router.get("/export/markdown")
def export_markdown(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> PlainTextResponse:
    return PlainTextResponse(
        reports.daily_report_markdown(db, user.id, date.today()), media_type="text/markdown"
    )


@router.post("/configuracoes/excluir-dados")
def delete_data(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> RedirectResponse:
    # MVP single-user cleanup. Keeps the user account so local auth remains simple.
    for model in [Activity, DailyRecovery, Sleep, SubjectiveCheckin, ImportJob]:
        for row in db.scalars(select(model).where(model.user_id == user.id)).all():
            db.delete(row)
    db.commit()
    return RedirectResponse("/hoje", status_code=303)
