"""Tests for settings validation."""

import pytest
from pydantic import ValidationError

from ariaops_mcp.config import Settings

_HS256_SECRET = "0123456789abcdef0123456789abcdef"  # 32 bytes — meets HS256 strength check
_RSA_PUBKEY_PEM_PLACEHOLDER = (
    "-----BEGIN PUBLIC KEY-----\nMIIBIjANBg-ignored-for-config-validation\n-----END PUBLIC KEY-----"
)


def test_reject_host_with_scheme(monkeypatch):
    monkeypatch.setenv("ARIAOPS_HOST", "https://vrops.test.local")
    monkeypatch.setenv("ARIAOPS_USERNAME", "testuser")
    monkeypatch.setenv("ARIAOPS_PASSWORD", "testpass")

    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_reject_invalid_transport(monkeypatch):
    monkeypatch.setenv("ARIAOPS_HOST", "vrops.test.local")
    monkeypatch.setenv("ARIAOPS_USERNAME", "testuser")
    monkeypatch.setenv("ARIAOPS_PASSWORD", "testpass")
    monkeypatch.setenv("ARIAOPS_TRANSPORT", "grpc")

    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_transport_and_log_level_normalized(monkeypatch):
    monkeypatch.setenv("ARIAOPS_HOST", "vrops.test.local")
    monkeypatch.setenv("ARIAOPS_USERNAME", "testuser")
    monkeypatch.setenv("ARIAOPS_PASSWORD", "testpass")
    monkeypatch.setenv("ARIAOPS_TRANSPORT", "HTTP")
    monkeypatch.setenv("ARIAOPS_LOG_LEVEL", "debug")

    settings = Settings()  # type: ignore[call-arg]
    assert settings.transport == "http"
    assert settings.log_level == "DEBUG"


def test_write_operations_disabled_by_default(monkeypatch):
    monkeypatch.setenv("ARIAOPS_HOST", "vrops.test.local")
    monkeypatch.setenv("ARIAOPS_USERNAME", "testuser")
    monkeypatch.setenv("ARIAOPS_PASSWORD", "testpass")

    settings = Settings()  # type: ignore[call-arg]
    assert settings.enable_write_operations is False


def test_write_operations_enabled(monkeypatch):
    monkeypatch.setenv("ARIAOPS_HOST", "vrops.test.local")
    monkeypatch.setenv("ARIAOPS_USERNAME", "testuser")
    monkeypatch.setenv("ARIAOPS_PASSWORD", "testpass")
    monkeypatch.setenv("ARIAOPS_ENABLE_WRITE_OPERATIONS", "true")

    settings = Settings()  # type: ignore[call-arg]
    assert settings.enable_write_operations is True


def test_write_operations_false_string(monkeypatch):
    monkeypatch.setenv("ARIAOPS_HOST", "vrops.test.local")
    monkeypatch.setenv("ARIAOPS_USERNAME", "testuser")
    monkeypatch.setenv("ARIAOPS_PASSWORD", "testpass")
    monkeypatch.setenv("ARIAOPS_ENABLE_WRITE_OPERATIONS", "false")

    settings = Settings()  # type: ignore[call-arg]
    assert settings.enable_write_operations is False


def test_http_oauth_enabled_requires_http_transport(monkeypatch):
    monkeypatch.setenv("ARIAOPS_HOST", "vrops.test.local")
    monkeypatch.setenv("ARIAOPS_USERNAME", "testuser")
    monkeypatch.setenv("ARIAOPS_PASSWORD", "testpass")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_ENABLED", "true")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_ISSUER_URL", "https://issuer.example.com")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL", "https://mcp.example.com")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_JWT_KEY", _HS256_SECRET)

    with pytest.raises(ValidationError, match="requires ARIAOPS_TRANSPORT=http"):
        Settings()  # type: ignore[call-arg]


def test_http_oauth_requires_complete_configuration(monkeypatch):
    monkeypatch.setenv("ARIAOPS_HOST", "vrops.test.local")
    monkeypatch.setenv("ARIAOPS_USERNAME", "testuser")
    monkeypatch.setenv("ARIAOPS_PASSWORD", "testpass")
    monkeypatch.setenv("ARIAOPS_TRANSPORT", "http")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_ENABLED", "true")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_ISSUER_URL", "https://issuer.example.com")

    with pytest.raises(ValidationError, match="ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL"):
        Settings()  # type: ignore[call-arg]


def test_http_oauth_list_settings_normalized(monkeypatch):
    monkeypatch.setenv("ARIAOPS_HOST", "vrops.test.local")
    monkeypatch.setenv("ARIAOPS_USERNAME", "testuser")
    monkeypatch.setenv("ARIAOPS_PASSWORD", "testpass")
    monkeypatch.setenv("ARIAOPS_TRANSPORT", "http")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_ENABLED", "true")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_ISSUER_URL", "https://issuer.example.com")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL", "https://mcp.example.com")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_JWT_KEY", _HS256_SECRET)
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES", "mcp:read, mcp:write")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_JWT_ALGORITHMS", "[\"HS256\", \"HS512\"]")

    settings = Settings()  # type: ignore[call-arg]
    assert settings.http_oauth_required_scopes == ["mcp:read", "mcp:write"]
    assert settings.http_oauth_jwt_algorithms == ["HS256", "HS512"]


def _base_oauth_env(monkeypatch):
    monkeypatch.setenv("ARIAOPS_HOST", "vrops.test.local")
    monkeypatch.setenv("ARIAOPS_USERNAME", "testuser")
    monkeypatch.setenv("ARIAOPS_PASSWORD", "testpass")
    monkeypatch.setenv("ARIAOPS_TRANSPORT", "http")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_ENABLED", "true")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_ISSUER_URL", "https://issuer.example.com")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL", "https://mcp.example.com")


def test_http_oauth_rejects_short_hmac_key(monkeypatch):
    _base_oauth_env(monkeypatch)
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_JWT_KEY", "too-short")

    with pytest.raises(ValidationError, match="must be at least 32 bytes"):
        Settings()  # type: ignore[call-arg]


def test_http_oauth_requires_key_or_jwks(monkeypatch):
    _base_oauth_env(monkeypatch)
    # Neither JWT_KEY nor JWKS_URL set
    with pytest.raises(ValidationError, match="ARIAOPS_HTTP_OAUTH_JWT_KEY.*ARIAOPS_HTTP_OAUTH_JWKS_URL"):
        Settings()  # type: ignore[call-arg]


def test_http_oauth_rejects_both_key_and_jwks(monkeypatch):
    _base_oauth_env(monkeypatch)
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_JWT_KEY", _HS256_SECRET)
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_JWKS_URL", "https://issuer.example.com/jwks")

    with pytest.raises(ValidationError, match="Set only one of"):
        Settings()  # type: ignore[call-arg]


def test_http_oauth_rejects_hmac_with_jwks(monkeypatch):
    _base_oauth_env(monkeypatch)
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_JWKS_URL", "https://issuer.example.com/jwks")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_JWT_ALGORITHMS", "HS256")

    with pytest.raises(ValidationError, match="HMAC algorithms.*incompatible with JWKS"):
        Settings()  # type: ignore[call-arg]


def test_http_oauth_jwks_with_rs256_accepted(monkeypatch):
    _base_oauth_env(monkeypatch)
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_JWKS_URL", "https://issuer.example.com/jwks")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_JWT_ALGORITHMS", "RS256")

    settings = Settings()  # type: ignore[call-arg]
    assert settings.http_oauth_jwt_algorithms == ["RS256"]
    assert str(settings.http_oauth_jwks_url) == "https://issuer.example.com/jwks"


def test_http_oauth_keycloak_provider_derives_jwks_and_rs256(monkeypatch):
    _base_oauth_env(monkeypatch)
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_PROVIDER", "keycloak")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_ISSUER_URL", "https://kc.example.com/realms/myrealm")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_AUDIENCE", "mcp-client")

    settings = Settings()  # type: ignore[call-arg]
    assert settings.http_oauth_provider == "keycloak"
    assert str(settings.http_oauth_jwks_url) == "https://kc.example.com/realms/myrealm/protocol/openid-connect/certs"
    assert settings.http_oauth_jwt_algorithms == ["RS256"]


def test_http_oauth_keycloak_provider_preserves_explicit_algorithms(monkeypatch):
    _base_oauth_env(monkeypatch)
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_PROVIDER", "keycloak")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_JWT_ALGORITHMS", "RS384,RS512")

    settings = Settings()  # type: ignore[call-arg]
    assert settings.http_oauth_jwt_algorithms == ["RS384", "RS512"]


def test_http_oauth_negative_leeway_rejected(monkeypatch):
    _base_oauth_env(monkeypatch)
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_JWT_KEY", _HS256_SECRET)
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_LEEWAY_SECONDS", "-1")

    with pytest.raises(ValidationError, match="LEEWAY_SECONDS must be >= 0"):
        Settings()  # type: ignore[call-arg]


def test_http_oauth_default_leeway(monkeypatch):
    _base_oauth_env(monkeypatch)
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_JWT_KEY", _HS256_SECRET)

    settings = Settings()  # type: ignore[call-arg]
    assert settings.http_oauth_leeway_seconds == 30
    assert settings.http_oauth_jwks_cache_ttl == 300
