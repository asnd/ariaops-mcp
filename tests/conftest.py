"""Shared pytest fixtures."""

import pytest

from ariaops_mcp import client as client_module


@pytest.fixture(autouse=True)
def reset_client():
    """Reset the module-level singleton before each test."""
    client_module._client = None
    yield
    client_module._client = None


@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("ARIAOPS_HOST", "vrops.test.local")
    monkeypatch.setenv("ARIAOPS_USERNAME", "testuser")
    monkeypatch.setenv("ARIAOPS_PASSWORD", "testpass")
    monkeypatch.setenv("ARIAOPS_VERIFY_SSL", "false")


TOKEN_RESPONSE = {
    "token": "test-token-abc123",
    "validity": 9999999999000,  # far future ms timestamp
    "expiresAt": "2099-01-01T00:00:00Z",
}
