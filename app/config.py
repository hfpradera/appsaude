from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Humberto Performance"
    app_env: str = "development"
    app_secret_key: str = "dev-only-change-me"
    app_local_password: str = "humberto-dev"
    database_url: str = "sqlite:///./data/humberto_performance.db"
    app_timezone: str = "America/Sao_Paulo"
    app_demo_data: bool = True
    hide_location: bool = True
    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_redirect_uri: str = "http://127.0.0.1:8000/integrations/strava/callback"
    strava_enabled: bool = False
    token_encryption_key: str = ""
    strava_sync_overlap_minutes: int = 60
    strava_sync_per_page: int = 100
    strava_http_timeout_seconds: int = 15
    strava_refresh_margin_seconds: int = 300
    upload_dir: Path = Path("./data/uploads")
    export_dir: Path = Path("./data/exports")

    @property
    def strava_configured(self) -> bool:
        return self.strava_enabled and bool(self.strava_client_id and self.strava_client_secret and self.token_encryption_key)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    Path("./data").mkdir(exist_ok=True)
    return settings
