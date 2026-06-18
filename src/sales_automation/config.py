from __future__ import annotations

from functools import lru_cache
import os

from dotenv import load_dotenv
from pydantic import Field
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    database_url: str = Field(alias="DATABASE_URL")
    database_pool_size: int = Field(default=5, alias="DATABASE_POOL_SIZE")
    database_max_overflow: int = Field(default=10, alias="DATABASE_MAX_OVERFLOW")
    database_echo: bool = Field(default=False, alias="DATABASE_ECHO")
    nvidia_api_key: str | None = Field(default=None, alias="NVIDIA_API_KEY")
    nvidia_model: str = Field(default="meta/llama-3.3-70b-instruct", alias="NVIDIA_MODEL")
    nvidia_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1", alias="NVIDIA_BASE_URL"
    )
    http_timeout_seconds: int = Field(default=15, alias="HTTP_TIMEOUT_SECONDS")
    ai_timeout_seconds: int = Field(default=60, alias="AI_TIMEOUT_SECONDS")
    ai_max_retries: int = Field(default=2, alias="AI_MAX_RETRIES")
    http_max_retries: int = Field(default=3, alias="HTTP_MAX_RETRIES")
    website_max_content_chars: int = Field(default=12_000, alias="WEBSITE_MAX_CONTENT_CHARS")
    gmail_credentials_file: str | None = Field(default=None, alias="GMAIL_CREDENTIALS_FILE")
    gmail_token_file: str | None = Field(default=None, alias="GMAIL_TOKEN_FILE")
    gmail_sender_email: str | None = Field(default=None, alias="GMAIL_SENDER_EMAIL")
    outreach_sender_name: str | None = Field(default=None, alias="OUTREACH_SENDER_NAME")
    outreach_reply_to_email: str | None = Field(default=None, alias="OUTREACH_REPLY_TO_EMAIL")
    outreach_unsubscribe_url: str | None = Field(default=None, alias="OUTREACH_UNSUBSCRIBE_URL")
    outreach_postal_address: str | None = Field(default=None, alias="OUTREACH_POSTAL_ADDRESS")
    slack_webhook_url: str | None = Field(default=None, alias="SLACK_WEBHOOK_URL")
    google_sheets_credentials_file: str | None = Field(
        default=None, alias="GOOGLE_SHEETS_CREDENTIALS_FILE"
    )
    google_sheets_spreadsheet_id: str | None = Field(
        default=None, alias="GOOGLE_SHEETS_SPREADSHEET_ID"
    )
    google_sheets_worksheet_name: str = Field(default="Workflow Log", alias="GOOGLE_SHEETS_WORKSHEET")
    approval_base_url: str = Field(default="http://localhost:8000", alias="APPROVAL_BASE_URL")
    email_host: str = Field(default="smtp.gmail.com", alias="EMAIL_HOST")
    email_port: int = Field(default=587, alias="EMAIL_PORT")
    email_use_tls: bool = Field(default=True, alias="EMAIL_USE_TLS")
    email_host_user: str | None = Field(default=None, alias="EMAIL_HOST_USER")
    email_host_password: str | None = Field(default=None, alias="EMAIL_HOST_PASSWORD")
    email_from_name: str = Field(default="Sales Automation", alias="EMAIL_FROM_NAME")
    otp_expiry_minutes: int = Field(default=10, alias="OTP_EXPIRY_MINUTES")
    otp_max_attempts: int = Field(default=5, alias="OTP_MAX_ATTEMPTS")
    auth_secret_key: str = Field(default="change-me-in-development", alias="AUTH_SECRET_KEY")
    auth_token_expiry_minutes: int = Field(default=1440, alias="AUTH_TOKEN_EXPIRY_MINUTES")
    auth_refresh_token_expiry_days: int = Field(default=30, alias="AUTH_REFRESH_TOKEN_EXPIRY_DAYS")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    if not os.getenv("DATABASE_URL"):
        os.environ.setdefault(
            "DATABASE_URL",
            "postgresql+psycopg://sales:sales@localhost:5432/sales_automation",
        )
    return Settings()
