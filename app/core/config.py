from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Professor AI Workspace"
    environment: str = "local"
    secret_key: str = "local-development-only-change-me"
    database_url: str = "sqlite:///./data/professor_ai.db"
    data_dir: Path = Path("data")
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_pool_recycle_seconds: int = 1800
    database_connect_timeout_seconds: int = 10

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
    email_provider: str = "smtp"
    ses_region: str | None = None
    ses_from: str | None = None
    require_verified_email_for_otp: bool = True
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
    ocr_enabled: bool = True
    ocr_provider: str = "local"
    ocr_language: str = "eng"
    ocr_dpi: int = 200
    ocr_min_text_chars: int = 80
    ocr_max_pages: int = 100

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value.removeprefix("postgres://")
        if value.startswith("postgresql://") and "+psycopg" not in value:
            return "postgresql+psycopg://" + value.removeprefix("postgresql://")
        return value

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def is_postgresql(self) -> bool:
        return self.database_url.startswith("postgresql+psycopg://")

    @property
    def resolved_ses_region(self) -> str:
        return self.ses_region or self.aws_region

    @property
    def resolved_from_email(self) -> str:
        return (self.ses_from or self.smtp_from).strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
