"""Configuration loaded from environment variables."""

import json
from contextvars import ContextVar, Token
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import AnyHttpUrl, Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode


class Settings(BaseSettings):
    host: str = Field("localhost", alias="ARIAOPS_HOST")
    username: str = Field("", alias="ARIAOPS_USERNAME")
    password: str = Field("", alias="ARIAOPS_PASSWORD")
    auth_source: str = Field("local", alias="ARIAOPS_AUTH_SOURCE")
    verify_ssl: bool = Field(True, alias="ARIAOPS_VERIFY_SSL")
    transport: Literal["stdio", "http"] = Field("stdio", alias="ARIAOPS_TRANSPORT")
    port: int = Field(8080, alias="ARIAOPS_PORT")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field("INFO", alias="ARIAOPS_LOG_LEVEL")
    log_format: Literal["text", "json"] = Field("text", alias="ARIAOPS_LOG_FORMAT")
    enable_write_operations: bool = Field(False, alias="ARIAOPS_ENABLE_WRITE_OPERATIONS")

    # Multi-instance support
    instances_file: str | None = Field(None, alias="ARIAOPS_INSTANCES_FILE")
    default_instance: str | None = Field(None, alias="ARIAOPS_DEFAULT_INSTANCE")
    http_oauth_enabled: bool = Field(False, alias="ARIAOPS_HTTP_OAUTH_ENABLED")
    http_oauth_issuer_url: AnyHttpUrl | None = Field(None, alias="ARIAOPS_HTTP_OAUTH_ISSUER_URL")
    http_oauth_resource_server_url: AnyHttpUrl | None = Field(None, alias="ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL")
    http_oauth_required_scopes: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        alias="ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES",
    )
    http_oauth_jwt_key: str | None = Field(None, alias="ARIAOPS_HTTP_OAUTH_JWT_KEY")
    http_oauth_jwks_url: AnyHttpUrl | None = Field(None, alias="ARIAOPS_HTTP_OAUTH_JWKS_URL")
    http_oauth_jwt_algorithms: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["HS256"],
        alias="ARIAOPS_HTTP_OAUTH_JWT_ALGORITHMS",
    )
    http_oauth_audience: str | None = Field(None, alias="ARIAOPS_HTTP_OAUTH_AUDIENCE")
    http_oauth_leeway_seconds: int = Field(30, alias="ARIAOPS_HTTP_OAUTH_LEEWAY_SECONDS")
    http_oauth_jwks_cache_ttl: int = Field(300, alias="ARIAOPS_HTTP_OAUTH_JWKS_CACHE_TTL")

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

    @field_validator("http_oauth_required_scopes", "http_oauth_jwt_algorithms", mode="before")
    @classmethod
    def normalize_string_list(cls, value: Any, info: ValidationInfo) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                parsed = json.loads(stripped)
                if not isinstance(parsed, list):
                    raise ValueError(f"Expected a JSON array for {info.field_name}")
                return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in stripped.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise ValueError("Expected a comma-separated string or list")

    @field_validator("host")
    @classmethod
    def host_must_not_include_scheme(cls, value: str) -> str:
        if "://" in value:
            raise ValueError("ARIAOPS_HOST should be hostname only (no scheme)")
        return value

    @model_validator(mode="after")
    def validate_http_oauth(self) -> "Settings":
        if not self.http_oauth_enabled:
            return self
        if self.transport != "http":
            raise ValueError("ARIAOPS_HTTP_OAUTH_ENABLED requires ARIAOPS_TRANSPORT=http")

        required_fields = {
            "ARIAOPS_HTTP_OAUTH_ISSUER_URL": self.http_oauth_issuer_url,
            "ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL": self.http_oauth_resource_server_url,
        }
        missing = [name for name, value in required_fields.items() if not value]
        if missing:
            raise ValueError(f"HTTP OAuth requires: {', '.join(missing)}")

        if not self.http_oauth_jwt_algorithms:
            raise ValueError("HTTP OAuth requires at least one JWT algorithm")

        if not self.http_oauth_jwt_key and not self.http_oauth_jwks_url:
            raise ValueError(
                "HTTP OAuth requires one of ARIAOPS_HTTP_OAUTH_JWT_KEY "
                "(static secret/PEM) or ARIAOPS_HTTP_OAUTH_JWKS_URL (e.g. Keycloak's "
                "/realms/<realm>/protocol/openid-connect/certs)"
            )
        if self.http_oauth_jwt_key and self.http_oauth_jwks_url:
            raise ValueError(
                "Set only one of ARIAOPS_HTTP_OAUTH_JWT_KEY or "
                "ARIAOPS_HTTP_OAUTH_JWKS_URL, not both"
            )

        hmac_algs = {a for a in self.http_oauth_jwt_algorithms if a.startswith("HS")}
        if hmac_algs and self.http_oauth_jwks_url:
            raise ValueError(
                "HMAC algorithms (HS256/384/512) are incompatible with JWKS — "
                "JWKS is for asymmetric keys (RS*/ES*/PS*). "
                "Either remove HS* from ARIAOPS_HTTP_OAUTH_JWT_ALGORITHMS or "
                "switch to ARIAOPS_HTTP_OAUTH_JWT_KEY."
            )
        # If any HMAC algorithm is configured, the shared secret must be long
        # enough to resist offline brute force. RFC 7518 §3.2 requires the key
        # be at least as long as the hash output (HS256 → 32 bytes).
        if hmac_algs:
            min_bytes = 32
            key_bytes = len((self.http_oauth_jwt_key or "").encode("utf-8"))
            if key_bytes < min_bytes:
                raise ValueError(
                    f"ARIAOPS_HTTP_OAUTH_JWT_KEY must be at least {min_bytes} bytes "
                    f"when an HMAC algorithm is used (got {key_bytes}). "
                    "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
                )

        if self.http_oauth_leeway_seconds < 0:
            raise ValueError("ARIAOPS_HTTP_OAUTH_LEEWAY_SECONDS must be >= 0")
        if self.http_oauth_jwks_cache_ttl < 0:
            raise ValueError("ARIAOPS_HTTP_OAUTH_JWKS_CACHE_TTL must be >= 0")

        return self

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
    """Invalidate the settings cache and clear cached tool registry."""
    _get_cached_settings.cache_clear()
    # Also invalidate the server-level tool registry cache so write-ops
    # status is re-evaluated on next access after settings change.
    try:
        import ariaops_mcp.server as _svr
        _svr._tool_defs = None
        _svr._tool_handlers = None
        _svr._TOOL_DEFS = None
        _svr._TOOL_HANDLERS = None
    except Exception:
        pass
