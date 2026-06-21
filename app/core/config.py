from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Professor AI Workspace"
    environment: str = "local"
    secret_key: str = "local-development-only-change-me"
    database_url: str = "sqlite:///./data/professor_ai.db"
    data_dir: Path = Path("data")

    openai_api_key: str | None = None
    openai_model: str = "gpt-5.5"
    openai_reasoning_effort: str = "medium"
    llm_service_mode: str = "openai"

    auth_enabled: bool = True
    otp_dev_mode: bool = True
    otp_ttl_minutes: int = 10
    session_ttl_minutes: int = 720
    cookie_name: str = "professor_ai_session"
    cookie_secure: bool = False
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "no-reply@example.edu"

    storage_provider: str = "local"
    s3_bucket: str | None = None
    s3_prefix: str = "professor-ai"
    s3_kms_key_id: str | None = None
    aws_region: str = "ap-south-1"
    max_upload_mb: int = 50
    max_context_chars: int = 120_000

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
