from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Humberto Performance"
    app_env: str = "development"
    app_public_base_url: str = "http://127.0.0.1:8000"
    app_secret_key: str = "dev-only-change-me"
    app_local_password: str = "humberto-dev"
    database_url: str = "sqlite:///./data/humberto_performance.db"
    app_timezone: str = "America/Sao_Paulo"
    app_demo_data: bool = True
    hide_location: bool = True
    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_redirect_uri: str = ""
    strava_enabled: bool = False
    token_encryption_key: str = ""
    strava_sync_overlap_minutes: int = 60
    strava_sync_per_page: int = 100
    strava_sync_max_activities_per_run: int = 1500
    strava_sync_max_runtime_seconds: int = 7200
    strava_http_timeout_seconds: int = 15
    strava_refresh_margin_seconds: int = 300
    whoop_client_id: str = ""
    whoop_client_secret: str = ""
    whoop_redirect_uri: str = ""
    whoop_enabled: bool = True
    whoop_http_timeout_seconds: int = 15
    whoop_refresh_margin_seconds: int = 300
    whoop_sync_lookback_days: int = 30
    whoop_sync_page_limit: int = 25
    whoop_auto_sync_enabled: bool = True
    whoop_auto_sync_hour: int = 4
    auto_sync_enabled: bool = True
    auto_sync_hours: str = "06,14,22"
    ai_enabled: bool = False
    ai_provider: str = "local"
    ai_daily_auto_enabled: bool = False
    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini"
    openai_chat_enabled: bool = False
    openai_timeout_seconds: int = 30
    openai_max_output_tokens: int = 2500
    openai_max_tool_rounds: int = 4
    openai_daily_request_limit: int = 50
    openai_monthly_budget_brl: float | None = None
    ai_chat_timeout_seconds: int = 30
    ai_chat_max_message_chars: int = 4000
    ai_chat_rate_limit_per_minute: int = 20
    ai_conversation_summary_message_limit: int = 30
    upload_dir: Path = Path("./data/uploads")
    export_dir: Path = Path("./data/exports")

    @model_validator(mode="after")
    def normalize_urls(self) -> "Settings":
        self.app_public_base_url = self.app_public_base_url.rstrip("/")
        return self

    @property
    def strava_configured(self) -> bool:
        return self.strava_enabled and bool(self.strava_client_id and self.strava_client_secret and self.token_encryption_key)

    @property
    def effective_strava_redirect_uri(self) -> str:
        return self.strava_redirect_uri or f"{self.app_public_base_url}/integrations/strava/callback"

    @property
    def whoop_configured(self) -> bool:
        return self.whoop_enabled and bool(
            self.whoop_client_id and self.whoop_client_secret and self.token_encryption_key
        )

    @property
    def effective_whoop_redirect_uri(self) -> str:
        return self.whoop_redirect_uri or f"{self.app_public_base_url}/integrations/whoop/callback"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    Path("./data").mkdir(exist_ok=True)
    return settings
