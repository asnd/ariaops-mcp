"""Configuration loaded from settings.ini and environment variables."""

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SETTINGS_FILE = Path("settings.ini")
logger = logging.getLogger(__name__)


def load_settings_ini(path: str | Path = SETTINGS_FILE) -> dict[str, str]:
    """Load key/value pairs from settings.ini if present.

    Missing files intentionally resolve to an empty mapping so callers can
    continue with environment variables and runtime prompts.
    """
    values = dotenv_values(path)
    return {key: value for key, value in values.items() if key and value}


class Settings(BaseSettings):
    host: str = Field(..., alias="ARIAOPS_HOST")
    username: str = Field(..., alias="ARIAOPS_USERNAME")
    password: str = Field(..., alias="ARIAOPS_PASSWORD")
    auth_source: str = Field("local", alias="ARIAOPS_AUTH_SOURCE")
    verify_ssl: bool = Field(False, alias="ARIAOPS_VERIFY_SSL")
    transport: Literal["stdio", "http"] = Field("stdio", alias="ARIAOPS_TRANSPORT")
    port: int = Field(443, alias="ARIAOPS_PORT")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field("DEBUG", alias="ARIAOPS_LOG_LEVEL")
    enable_write_operations: bool = Field(False, alias="ARIAOPS_ENABLE_WRITE_OPERATIONS")

    model_config = SettingsConfigDict(populate_by_name=True, env_file="settings.ini", env_file_encoding="utf-8")

    @field_validator("transport", mode="before")
    @classmethod
    def normalize_transport(cls, value: str) -> str:
        return value.lower()

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @field_validator("host")
    @classmethod
    def host_must_not_include_scheme(cls, value: str) -> str:
        if "://" in value:
            raise ValueError("ARIAOPS_HOST should be hostname only (no scheme)")
        return value

    @property
    def base_url(self) -> str:
        return f"https://{self.host}/suite-api/api"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def write_operations_enabled() -> bool:
    try:
        return bool(get_settings().enable_write_operations)
    except (ValidationError, ValueError) as exc:
        logger.warning(
            "Failed to resolve ARIAOPS_ENABLE_WRITE_OPERATIONS from settings, defaulting to disabled: %s",
            exc,
        )
        return False
