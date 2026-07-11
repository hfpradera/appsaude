from datetime import date

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Activity, DailyRecovery, ImportJob, Sleep, SubjectiveCheckin, User
from app.security import make_session_cookie, password_matches, verify_session_cookie
from app.services import reports
from app.services.importers import import_file, save_upload
from app.services.timezone import seconds_to_human, to_local

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["seconds"] = seconds_to_human
templates.env.filters["localdt"] = lambda value: to_local(value).strftime("%d/%m/%Y %H:%M") if value else "-"


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
        return render(request, "login.html", {"request": request, "error": "Senha invalida"}, status_code=401)
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
def today_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)) -> HTMLResponse:
    data = reports.dashboard(db, user.id, date.today())
    return render(request, "dashboard.html", {"request": request, "user": user, "data": data})


@router.get("/atividades", response_class=HTMLResponse)
def activities_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)) -> HTMLResponse:
    activities = db.scalars(select(Activity).where(Activity.user_id == user.id).order_by(Activity.started_at.desc())).all()
    return render(request, "activities.html", {"request": request, "user": user, "activities": activities})


@router.get("/atividades/{activity_id}", response_class=HTMLResponse)
def activity_detail(activity_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)) -> HTMLResponse:
    activity = db.get(Activity, activity_id)
    if not activity or activity.user_id != user.id:
        return render(request, "not_found.html", {"request": request, "user": user}, status_code=404)
    return render(request, "activity_detail.html", {"request": request, "user": user, "activity": activity})


@router.get("/sono", response_class=HTMLResponse)
def sleep_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)) -> HTMLResponse:
    sleeps = db.scalars(select(Sleep).where(Sleep.user_id == user.id).order_by(Sleep.day.desc())).all()
    return render(request, "sleep.html", {"request": request, "user": user, "sleeps": sleeps})


@router.get("/recuperacao", response_class=HTMLResponse)
def recovery_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)) -> HTMLResponse:
    recoveries = db.scalars(select(DailyRecovery).where(DailyRecovery.user_id == user.id).order_by(DailyRecovery.day.desc())).all()
    return render(request, "recovery.html", {"request": request, "user": user, "recoveries": recoveries})


@router.get("/checkin", response_class=HTMLResponse)
def checkin_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)) -> HTMLResponse:
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
    checkin = reports.latest_checkin(db, user.id, date.today()) or SubjectiveCheckin(user_id=user.id, day=date.today())
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
def weekly_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)) -> HTMLResponse:
    report = reports.weekly_report(db, user.id, date.today())
    daily = reports.daily_report_markdown(db, user.id, date.today())
    return render(request, "weekly_report.html", {"request": request, "user": user, "report": report, "daily": daily})


@router.get("/importacoes", response_class=HTMLResponse)
def imports_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)) -> HTMLResponse:
    jobs = db.scalars(select(ImportJob).where(ImportJob.user_id == user.id).order_by(ImportJob.created_at.desc())).all()
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
def integrations_page(request: Request, user: User = Depends(current_user)) -> HTMLResponse:
    return render(request, "integrations.html", {"request": request, "user": user})


@router.get("/configuracoes", response_class=HTMLResponse)
def settings_page(request: Request, user: User = Depends(current_user)) -> HTMLResponse:
    return render(request, "settings.html", {"request": request, "user": user, "settings": get_settings()})


@router.get("/export/json")
def export_json(db: Session = Depends(get_db), user: User = Depends(current_user)) -> Response:
    return Response(reports.export_json(db, user.id, date.today()), media_type="application/json")


@router.get("/export/markdown")
def export_markdown(db: Session = Depends(get_db), user: User = Depends(current_user)) -> PlainTextResponse:
    return PlainTextResponse(reports.daily_report_markdown(db, user.id, date.today()), media_type="text/markdown")


@router.post("/configuracoes/excluir-dados")
def delete_data(db: Session = Depends(get_db), user: User = Depends(current_user)) -> RedirectResponse:
    # MVP single-user cleanup. Keeps the user account so local auth remains simple.
    for model in [Activity, DailyRecovery, Sleep, SubjectiveCheckin, ImportJob]:
        for row in db.scalars(select(model).where(model.user_id == user.id)).all():
            db.delete(row)
    db.commit()
    return RedirectResponse("/hoje", status_code=303)
