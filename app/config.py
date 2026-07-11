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
    upload_dir: Path = Path("./data/uploads")
    export_dir: Path = Path("./data/exports")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    Path("./data").mkdir(exist_ok=True)
    return settings
