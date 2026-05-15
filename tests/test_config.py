"""Tests for settings validation."""

import pytest
from pydantic import ValidationError

from ariaops_mcp.config import Settings


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
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_JWT_KEY", "secret")

    with pytest.raises(ValidationError, match="ARIAOPS_HTTP_OAUTH_ENABLED requires ARIAOPS_TRANSPORT=http"):
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
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_JWT_KEY", "secret")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES", "mcp:read, mcp:write")
    monkeypatch.setenv("ARIAOPS_HTTP_OAUTH_JWT_ALGORITHMS", "[\"HS256\", \"HS512\"]")

    settings = Settings()  # type: ignore[call-arg]
    assert settings.http_oauth_required_scopes == ["mcp:read", "mcp:write"]
    assert settings.http_oauth_jwt_algorithms == ["HS256", "HS512"]
