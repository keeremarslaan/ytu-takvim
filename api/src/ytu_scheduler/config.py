from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="YTU_", extra="ignore")

    owner_token: SecretStr | None = None

    app_name: str = "YTU Schedule Lab V2"
    database_url: str = "sqlite:///./ytu_scheduler.db"
    cors_origins: str = "http://localhost:3000"
    draft_database: Path = Path("data/owner-drafts.db")
    upload_dir: Path = Path("data/uploads")
    max_upload_bytes: int = 20 * 1024 * 1024
    bologna_base_url: str = "https://bologna.yildiz.edu.tr"
    bologna_request_delay_seconds: float = 0.35

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
