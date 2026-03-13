"""Configuration loaded from environment variables."""

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

    model_config = {"populate_by_name": True}

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
