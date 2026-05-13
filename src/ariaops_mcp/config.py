"""Configuration loaded from environment variables."""

from contextvars import ContextVar, Token
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    host: str = Field(..., alias="ARIAOPS_HOST")
    username: str = Field(..., alias="ARIAOPS_USERNAME")
    password: str = Field(..., alias="ARIAOPS_PASSWORD")
    auth_source: str = Field("local", alias="ARIAOPS_AUTH_SOURCE")
    verify_ssl: bool = Field(True, alias="ARIAOPS_VERIFY_SSL")
    transport: Literal["stdio", "http"] = Field("stdio", alias="ARIAOPS_TRANSPORT")
    port: int = Field(8080, alias="ARIAOPS_PORT")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field("INFO", alias="ARIAOPS_LOG_LEVEL")
    log_format: Literal["text", "json"] = Field("text", alias="ARIAOPS_LOG_FORMAT")
    enable_write_operations: bool = Field(False, alias="ARIAOPS_ENABLE_WRITE_OPERATIONS")

    # Resilience
    request_deadline: float = Field(120.0, alias="ARIAOPS_REQUEST_DEADLINE")
    max_concurrent_requests: int = Field(10, alias="ARIAOPS_MAX_CONCURRENT_REQUESTS")

    # Circuit breaker
    cb_failure_threshold: int = Field(5, alias="ARIAOPS_CB_FAILURE_THRESHOLD")
    cb_recovery_timeout: int = Field(30, alias="ARIAOPS_CB_RECOVERY_TIMEOUT")
    cb_success_threshold: int = Field(2, alias="ARIAOPS_CB_SUCCESS_THRESHOLD")

    # Skills
    skills_dir: str | None = Field(None, alias="ARIAOPS_SKILLS_DIR")

    model_config = {"populate_by_name": True}

    @field_validator("transport", mode="before")
    @classmethod
    def normalize_transport(cls, value: str) -> str:
        return value.lower()

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @field_validator("log_format", mode="before")
    @classmethod
    def normalize_log_format(cls, value: str) -> str:
        return value.lower()

    @field_validator("host")
    @classmethod
    def host_must_not_include_scheme(cls, value: str) -> str:
        if "://" in value:
            raise ValueError("ARIAOPS_HOST should be hostname only (no scheme)")
        return value

    @property
    def base_url(self) -> str:
        return f"https://{self.host}/suite-api/api"


_settings_override: ContextVar[Settings | None] = ContextVar("ariaops_settings_override", default=None)


@lru_cache(maxsize=1)
def _get_cached_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def get_settings() -> Settings:
    override = _settings_override.get()
    if override is not None:
        return override
    return _get_cached_settings()


def set_settings_override(settings: Settings) -> Token[Settings | None]:
    return _settings_override.set(settings)


def reset_settings_override(token: Token[Settings | None]) -> None:
    _settings_override.reset(token)


def clear_settings_cache() -> None:
    _get_cached_settings.cache_clear()
