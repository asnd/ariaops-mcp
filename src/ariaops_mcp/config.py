"""Configuration loaded from environment variables."""

import json
import logging
from contextvars import ContextVar, Token
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import AnyHttpUrl, BaseModel, Field, TypeAdapter, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode

logger = logging.getLogger(__name__)

# Identifier used for the implicit single instance synthesized from the legacy
# ARIAOPS_HOST/USERNAME/PASSWORD variables when ARIAOPS_INSTANCES is not set.
DEFAULT_INSTANCE_ID = "default"


class InstanceConfig(BaseModel):
    """Connection settings for one Aria Operations instance.

    Multiple instances are configured via the ``ARIAOPS_INSTANCES`` JSON array;
    a single instance is also synthesized from the legacy ``ARIAOPS_HOST`` /
    ``ARIAOPS_USERNAME`` / ``ARIAOPS_PASSWORD`` variables for backward compat.
    """

    id: str
    host: str
    username: str
    password: str
    auth_source: str = "local"
    verify_ssl: bool = True
    # Optional country code used to map a "country" role user to this instance.
    country: str | None = None

    @field_validator("id", "host")
    @classmethod
    def _not_blank(cls, value: str, info: ValidationInfo) -> str:
        if not value or not value.strip():
            raise ValueError(f"Instance {info.field_name} must not be blank")
        return value.strip()

    @field_validator("host")
    @classmethod
    def _host_no_scheme(cls, value: str) -> str:
        if "://" in value:
            raise ValueError("Instance host should be hostname only (no scheme)")
        return value

    @field_validator("country")
    @classmethod
    def _normalize_country(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @property
    def base_url(self) -> str:
        return f"https://{self.host}/suite-api/api"


class Settings(BaseSettings):
    host: str | None = Field(None, alias="ARIAOPS_HOST")
    username: str | None = Field(None, alias="ARIAOPS_USERNAME")
    password: str | None = Field(None, alias="ARIAOPS_PASSWORD")
    auth_source: str = Field("local", alias="ARIAOPS_AUTH_SOURCE")
    verify_ssl: bool = Field(True, alias="ARIAOPS_VERIFY_SSL")

    # Multi-instance configuration (JSON array). When unset, a single instance is
    # synthesized from the legacy ARIAOPS_HOST/USERNAME/PASSWORD variables.
    instances: Annotated[list[InstanceConfig], NoDecode] = Field(
        default_factory=list,
        alias="ARIAOPS_INSTANCES",
    )

    # Role-based access. The role/country/instance are read from JWT claims on the
    # HTTP transport, or fall back to the *_default values for stdio/local use.
    role_claim: str = Field("ariaops_role", alias="ARIAOPS_ROLE_CLAIM")
    country_claim: str = Field("ariaops_country", alias="ARIAOPS_COUNTRY_CLAIM")
    instance_claim: str = Field("ariaops_instance", alias="ARIAOPS_INSTANCE_CLAIM")
    ops_role: str = Field("ops", alias="ARIAOPS_OPS_ROLE")
    country_role: str = Field("country", alias="ARIAOPS_COUNTRY_ROLE")
    default_role: str = Field("ops", alias="ARIAOPS_DEFAULT_ROLE")
    default_country: str | None = Field(None, alias="ARIAOPS_DEFAULT_COUNTRY")
    default_instance: str | None = Field(None, alias="ARIAOPS_DEFAULT_INSTANCE")
    transport: Literal["stdio", "http"] = Field("stdio", alias="ARIAOPS_TRANSPORT")
    port: int = Field(8080, alias="ARIAOPS_PORT")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field("INFO", alias="ARIAOPS_LOG_LEVEL")
    log_format: Literal["text", "json"] = Field("text", alias="ARIAOPS_LOG_FORMAT")
    enable_write_operations: bool = Field(False, alias="ARIAOPS_ENABLE_WRITE_OPERATIONS")
    http_oauth_enabled: bool = Field(False, alias="ARIAOPS_HTTP_OAUTH_ENABLED")
    http_oauth_provider: Literal["generic", "keycloak"] = Field("generic", alias="ARIAOPS_HTTP_OAUTH_PROVIDER")
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

    # HTTP auth backend selector: "oauth" (JWT bearer), "ldap" (HTTP Basic +
    # LDAPS bind), or "none". Defaults to "none"; http_oauth_enabled=true is a
    # backward-compatible alias for "oauth" (see effective_auth_mode).
    http_auth_mode: Literal["oauth", "ldap", "none"] = Field("none", alias="ARIAOPS_HTTP_AUTH_MODE")

    # LDAP/AD authentication (http_auth_mode=ldap). AD groups map to the same
    # role/country/instance claims that principal.py reads for OAuth.
    ldap_server_uri: str | None = Field(None, alias="ARIAOPS_LDAP_SERVER_URI")
    ldap_user_dn_template: str | None = Field(None, alias="ARIAOPS_LDAP_USER_DN_TEMPLATE")
    ldap_user_search_base: str | None = Field(None, alias="ARIAOPS_LDAP_USER_SEARCH_BASE")
    ldap_group_role_map: Annotated[dict[str, dict[str, str]], NoDecode] = Field(
        default_factory=dict,
        alias="ARIAOPS_LDAP_GROUP_ROLE_MAP",
    )
    ldap_ca_cert_file: str | None = Field(None, alias="ARIAOPS_LDAP_CA_CERT_FILE")
    ldap_verify_tls: bool = Field(True, alias="ARIAOPS_LDAP_VERIFY_TLS")
    ldap_cache_ttl: int = Field(300, alias="ARIAOPS_LDAP_CACHE_TTL")
    ldap_bind_timeout: int = Field(10, alias="ARIAOPS_LDAP_BIND_TIMEOUT")

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

    @field_validator("instances", mode="before")
    @classmethod
    def parse_instances(cls, value: Any) -> Any:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"ARIAOPS_INSTANCES must be a JSON array: {exc}") from exc
            if not isinstance(parsed, list):
                raise ValueError("ARIAOPS_INSTANCES must be a JSON array of instance objects")
            return parsed
        return value

    @field_validator("ldap_group_role_map", mode="before")
    @classmethod
    def parse_group_role_map(cls, value: Any) -> dict[str, dict[str, str]]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return {}
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"ARIAOPS_LDAP_GROUP_ROLE_MAP must be valid JSON: {exc}") from exc
            if not isinstance(parsed, dict):
                raise ValueError("ARIAOPS_LDAP_GROUP_ROLE_MAP must be a JSON object")
            return {str(k): {str(ik): str(iv) for ik, iv in v.items()} for k, v in parsed.items()}
        raise ValueError("Expected a JSON object string or dict for ARIAOPS_LDAP_GROUP_ROLE_MAP")

    @field_validator("host")
    @classmethod
    def host_must_not_include_scheme(cls, value: str | None) -> str | None:
        if value and "://" in value:
            raise ValueError("ARIAOPS_HOST should be hostname only (no scheme)")
        return value

    @model_validator(mode="after")
    def validate_instances(self) -> "Settings":
        if self.instances:
            ids = [inst.id for inst in self.instances]
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            if duplicates:
                raise ValueError(f"Duplicate instance id(s) in ARIAOPS_INSTANCES: {', '.join(duplicates)}")
            if self.default_instance and self.default_instance not in ids:
                raise ValueError(
                    f"ARIAOPS_DEFAULT_INSTANCE='{self.default_instance}' is not one of the "
                    f"configured instances: {', '.join(ids)}"
                )
        else:
            # Legacy single-instance mode requires the classic credentials.
            missing = [
                name
                for name, value in (
                    ("ARIAOPS_HOST", self.host),
                    ("ARIAOPS_USERNAME", self.username),
                    ("ARIAOPS_PASSWORD", self.password),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    "Provide either ARIAOPS_INSTANCES (JSON array) or the legacy "
                    f"single-instance variables. Missing: {', '.join(missing)}"
                )
        return self

    def resolved_instances(self) -> list[InstanceConfig]:
        """Return the configured instances, synthesizing a single one from legacy vars."""
        if self.instances:
            return list(self.instances)
        # host/username/password presence is guaranteed by validate_instances.
        return [
            InstanceConfig(
                id=self.default_instance or DEFAULT_INSTANCE_ID,
                host=self.host or "",
                username=self.username or "",
                password=self.password or "",
                auth_source=self.auth_source,
                verify_ssl=self.verify_ssl,
                country=self.default_country,
            )
        ]

    @property
    def default_instance_id(self) -> str:
        """The instance id used when no explicit instance is requested."""
        instances = self.resolved_instances()
        if self.default_instance:
            return self.default_instance
        return instances[0].id

    def get_instance(self, instance_id: str | None = None) -> InstanceConfig:
        """Look up an instance by id, defaulting to ``default_instance_id``."""
        instances = self.resolved_instances()
        target = instance_id or self.default_instance_id
        for inst in instances:
            if inst.id == target:
                return inst
        raise KeyError(f"Unknown Aria Operations instance: {target}")

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

    @field_validator("http_oauth_provider", mode="before")
    @classmethod
    def normalize_http_oauth_provider(cls, value: str) -> str:
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

    @model_validator(mode="after")
    def _check_auth_mode_conflict(self) -> "Settings":
        """Reject the impossible combination of oauth_enabled + ldap mode."""
        if self.http_oauth_enabled and self.http_auth_mode == "ldap":
            raise ValueError(
                "ARIAOPS_HTTP_OAUTH_ENABLED=true conflicts with "
                "ARIAOPS_HTTP_AUTH_MODE=ldap. Set only one, or use "
                "ARIAOPS_HTTP_AUTH_MODE=oauth."
            )
        return self

    @model_validator(mode="after")
    def validate_http_oauth(self) -> "Settings":
        # Activate when explicitly enabled (legacy flag) or selected via mode.
        if not self.http_oauth_enabled and self.http_auth_mode != "oauth":
            return self
        if self.transport != "http":
            raise ValueError(
                "HTTP OAuth (ARIAOPS_HTTP_OAUTH_ENABLED=true or "
                "ARIAOPS_HTTP_AUTH_MODE=oauth) requires ARIAOPS_TRANSPORT=http"
            )

        required_fields = {
            "ARIAOPS_HTTP_OAUTH_ISSUER_URL": self.http_oauth_issuer_url,
            "ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL": self.http_oauth_resource_server_url,
        }
        missing = [name for name, value in required_fields.items() if not value]
        if missing:
            raise ValueError(f"HTTP OAuth requires: {', '.join(missing)}")

        if self.http_oauth_provider == "keycloak":
            use_provider_jwks = not self.http_oauth_jwt_key
            if use_provider_jwks and "http_oauth_jwt_algorithms" not in self.model_fields_set:
                self.http_oauth_jwt_algorithms = ["RS256"]
            if use_provider_jwks and not self.http_oauth_jwks_url:
                issuer_url = str(self.http_oauth_issuer_url).rstrip("/")
                self.http_oauth_jwks_url = TypeAdapter(AnyHttpUrl).validate_python(
                    f"{issuer_url}/protocol/openid-connect/certs"
                )

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

    @model_validator(mode="after")
    def validate_http_ldap(self) -> "Settings":
        if self.http_auth_mode != "ldap":
            return self
        if self.transport != "http":
            raise ValueError("ARIAOPS_HTTP_AUTH_MODE=ldap requires ARIAOPS_TRANSPORT=http")

        missing: list[str] = []
        if not self.ldap_server_uri:
            missing.append("ARIAOPS_LDAP_SERVER_URI")
        if not self.ldap_user_dn_template:
            missing.append("ARIAOPS_LDAP_USER_DN_TEMPLATE")
        if not self.ldap_user_search_base:
            missing.append("ARIAOPS_LDAP_USER_SEARCH_BASE")
        if missing:
            raise ValueError(f"LDAP auth requires: {', '.join(missing)}")

        uri = self.ldap_server_uri or ""
        if not uri.lower().startswith("ldaps://") and self.ldap_verify_tls:
            raise ValueError(
                "ARIAOPS_LDAP_SERVER_URI must use ldaps:// when "
                "ARIAOPS_LDAP_VERIFY_TLS=true (default). Use ldaps:// or set "
                "ARIAOPS_LDAP_VERIFY_TLS=false (not recommended for production)."
            )

        if self.ldap_cache_ttl < 0:
            raise ValueError("ARIAOPS_LDAP_CACHE_TTL must be >= 0")
        if self.ldap_bind_timeout <= 0:
            raise ValueError("ARIAOPS_LDAP_BIND_TIMEOUT must be > 0")
        if self.http_oauth_required_scopes:
            logger.warning(
                "ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES is set but ignored in LDAP mode: "
                "LDAP-authenticated requests carry no OAuth scopes."
            )
        return self

    @property
    def effective_auth_mode(self) -> Literal["oauth", "ldap", "none"]:
        """Resolved HTTP auth mode, honoring the legacy http_oauth_enabled flag."""
        if self.http_auth_mode != "none":
            return self.http_auth_mode
        return "oauth" if self.http_oauth_enabled else "none"

    @property
    def base_url(self) -> str:
        """Base URL of the default instance (kept for backward compatibility)."""
        return self.get_instance().base_url


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
    # Drop any cached per-instance clients so they are rebuilt from fresh settings.
    try:
        import ariaops_mcp.client as _client
        _client.reset_client_cache()
    except Exception:
        pass
