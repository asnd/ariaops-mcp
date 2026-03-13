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
