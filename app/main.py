from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import SessionLocal, create_db
from app.logging_config import configure_logging
from app.routes import NotAuthenticated, router
from app.seed import seed_demo_data


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    app = FastAPI(title=settings.app_name)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(router)

    @app.exception_handler(NotAuthenticated)
    def not_authenticated(_request: Request, _exc: NotAuthenticated) -> RedirectResponse:
        return RedirectResponse("/login", status_code=303)

    @app.on_event("startup")
    def startup() -> None:
        create_db()
        if settings.app_demo_data:
            with SessionLocal() as db:
                seed_demo_data(db)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
