import os
from typing import Literal, cast

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"]
_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
_DEFAULT_DB_PATH = "/data/prices.db"


def _tokens_env_path() -> str:
    """Locate tokens.env next to the configured database, wherever that is.

    Read directly from the process environment (rather than a Settings field)
    because this path is needed to build the env_file tuple below, before any
    Settings instance exists. Docker mounts the DB at /data; native-cron
    deployments set DB_PATH to a different directory via a sourced .env file.
    """
    db_path = os.environ.get("DB_PATH", _DEFAULT_DB_PATH)
    return os.path.join(os.path.dirname(db_path) or ".", "tokens.env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", _tokens_env_path()), env_file_encoding="utf-8"
    )

    trakt_client_id: str
    trakt_client_secret: str
    trakt_access_token: str
    trakt_refresh_token: str
    trakt_username: str

    smtp_host: str
    smtp_port: int = 587
    smtp_username: str
    smtp_password: str
    smtp_from: str
    smtp_to: str

    discount_threshold_percent: float = Field(default=20.0, gt=0, le=100)
    sale_price_threshold: float = Field(default=5.0, ge=0)
    sale_list_slug: str = ""
    check_interval_hours: float = Field(default=6.0, gt=0)
    api_request_interval_seconds: float = Field(default=1.5, ge=0)
    db_path: str = _DEFAULT_DB_PATH
    log_level: LogLevel = "INFO"
    email_theme: str = "dark"
    app_version: str = ""
    run_once: bool = False

    @field_validator("email_theme", mode="before")
    @classmethod
    def normalize_email_theme(cls, value: object) -> str:
        if isinstance(value, str) and value.strip().lower() in {"dark", "light"}:
            return value.strip().lower()
        return "dark"

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> LogLevel:
        if isinstance(value, str):
            normalized = value.upper()
            if normalized in _LOG_LEVELS:
                return cast(LogLevel, normalized)
        msg = "LOG_LEVEL must be one of CRITICAL, ERROR, WARNING, INFO, DEBUG, or NOTSET"
        raise ValueError(msg)


settings = Settings()  # type: ignore[call-arg]
