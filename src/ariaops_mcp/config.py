"""Configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    host: str = Field(..., alias="ARIAOPS_HOST")
    username: str = Field(..., alias="ARIAOPS_USERNAME")
    password: str = Field(..., alias="ARIAOPS_PASSWORD")
    auth_source: str = Field("local", alias="ARIAOPS_AUTH_SOURCE")
    verify_ssl: bool = Field(True, alias="ARIAOPS_VERIFY_SSL")
    transport: str = Field("stdio", alias="ARIAOPS_TRANSPORT")
    port: int = Field(8080, alias="ARIAOPS_PORT")
    log_level: str = Field("INFO", alias="ARIAOPS_LOG_LEVEL")

    model_config = {"populate_by_name": True}

    @property
    def base_url(self) -> str:
        return f"https://{self.host}/suite-api/api"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
