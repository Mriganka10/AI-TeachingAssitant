import re
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
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
    openai_reasoning_effort: str = "low"
    openai_text_verbosity: str = "high"
    openai_max_output_tokens: int = 24_000
    openai_service_tier: str | None = None
    openai_prompt_cache_retention: str | None = None
    llm_repair_attempts: int = 1
    llm_service_mode: str = "openai"
    video_generation_enabled: bool = True
    video_min_minutes: int = 4
    video_max_minutes: int = 15
    video_script_model: str | None = None
    video_script_max_output_tokens: int = 14_000
    video_tts_provider: str = "openai"
    video_tts_model: str = "gpt-4o-mini-tts"
    video_tts_voice: str = "marin"
    video_tts_speed: float = 0.95
    elevenlabs_api_key: str | None = None
    elevenlabs_model_id: str = "eleven_multilingual_v2"
    elevenlabs_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"
    elevenlabs_output_format: str = "mp3_44100_128"
    elevenlabs_stability: float = 0.55
    elevenlabs_similarity_boost: float = 0.78

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
    agent_execution_backend: str = "background"
    agent_queue_url: str | None = None
    agent_queue_wait_seconds: int = 20
    agent_queue_visibility_timeout_seconds: int = 900
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

    @field_validator("video_min_minutes", "video_max_minutes")
    @classmethod
    def validate_video_minutes(cls, value: int) -> int:
        if not 3 <= value <= 15:
            raise ValueError("Video duration limits must be between 3 and 15 minutes.")
        return value

    @field_validator("video_tts_speed")
    @classmethod
    def validate_video_tts_speed(cls, value: float) -> float:
        if not 0.25 <= value <= 4.0:
            raise ValueError("VIDEO_TTS_SPEED must be between 0.25 and 4.0.")
        return value

    @field_validator("video_tts_provider")
    @classmethod
    def validate_video_tts_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"openai", "elevenlabs"}:
            raise ValueError("VIDEO_TTS_PROVIDER must be 'openai' or 'elevenlabs'.")
        return normalized

    @field_validator("elevenlabs_stability", "elevenlabs_similarity_boost")
    @classmethod
    def validate_elevenlabs_voice_setting(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("ElevenLabs voice settings must be between 0 and 1.")
        return value

    @field_validator("elevenlabs_output_format")
    @classmethod
    def validate_elevenlabs_output_format(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"mp3_\d+_\d+", normalized):
            raise ValueError(
                "ELEVENLABS_OUTPUT_FORMAT must be an MP3 format such as mp3_44100_128."
            )
        return normalized

    @model_validator(mode="after")
    def validate_video_duration_range(self):
        if self.video_min_minutes > self.video_max_minutes:
            raise ValueError("VIDEO_MIN_MINUTES cannot exceed VIDEO_MAX_MINUTES.")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def is_postgresql(self) -> bool:
        return self.database_url.startswith("postgresql+psycopg://")

    @property
    def uses_sqs_agent_queue(self) -> bool:
        return self.agent_execution_backend.lower() == "sqs"

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
