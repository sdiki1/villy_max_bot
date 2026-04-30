from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    max_bot_token: str = Field(default="", alias="MAX_BOT_TOKEN")
    max_skip_updates: bool = Field(default=True, alias="MAX_SKIP_UPDATES")
    welcome_image_path: str | None = Field(
        default=None,
        alias="WELCOME_IMAGE_PATH",
    )

    database_url: str = Field(
        default="postgresql+asyncpg://villyprint:villyprint@db:5432/villyprint",
        alias="DATABASE_URL",
    )

    admin_username: str = Field(default="admin", alias="ADMIN_USERNAME")
    admin_password: str = Field(default="admin123", alias="ADMIN_PASSWORD")
    admin_session_secret: str = Field(
        default="change-me-session-secret",
        alias="ADMIN_SESSION_SECRET",
    )

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    max_notify_chat_id: str = Field(
        default="-72352444311745",
        alias="MAX_NOTIFY_CHAT_ID",
    )
    admin_url: str = Field(
        default="http://localhost:8001/admin/chats",
        alias="ADMIN_URL",
    )

    wb_api_token: str = Field(default="", alias="WB_API_TOKEN")
    wb_auto_reply_poll_interval: int = Field(
        default=30,
        alias="WB_AUTO_REPLY_POLL_INTERVAL",
    )
    wb_api_min_interval_seconds: float = Field(
        default=0.36,
        alias="WB_API_MIN_INTERVAL_SECONDS",
    )
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.0-flash", alias="GEMINI_MODEL")
    gemini_temperature: float = Field(default=0.4, alias="GEMINI_TEMPERATURE")

    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
