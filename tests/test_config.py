"""Tests for settings validation."""

import pytest
from pydantic import ValidationError

from ariaops_mcp.config import Settings


def test_loads_required_values_from_settings_ini(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ARIAOPS_HOST", raising=False)
    monkeypatch.delenv("ARIAOPS_USERNAME", raising=False)
    monkeypatch.delenv("ARIAOPS_PASSWORD", raising=False)
    (tmp_path / "settings.ini").write_text(
        "ARIAOPS_HOST=vrops.test.local\n"
        "ARIAOPS_USERNAME=testuser\n"
        "ARIAOPS_PASSWORD=testpass\n",
        encoding="utf-8",
    )

    settings = Settings()  # type: ignore[call-arg]
    assert settings.host == "vrops.test.local"
    assert settings.username == "testuser"
    assert settings.password == "testpass"
    assert settings.auth_source == "local"
    assert settings.verify_ssl is False
    assert settings.transport == "stdio"
    assert settings.port == 443
    assert settings.log_level == "DEBUG"
    assert settings.enable_write_operations is False


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


def test_enable_write_operations_from_env(monkeypatch):
    monkeypatch.setenv("ARIAOPS_HOST", "vrops.test.local")
    monkeypatch.setenv("ARIAOPS_USERNAME", "testuser")
    monkeypatch.setenv("ARIAOPS_PASSWORD", "testpass")
    monkeypatch.setenv("ARIAOPS_ENABLE_WRITE_OPERATIONS", "true")

    settings = Settings()  # type: ignore[call-arg]
    assert settings.enable_write_operations is True
