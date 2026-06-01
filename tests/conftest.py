"""Shared pytest fixtures."""

import os

import pytest

from ariaops_mcp import client as client_module
from ariaops_mcp.config import clear_settings_cache

# Set default test env vars at module level so that modules which
# trigger Settings() at import time (e.g. server.py) can be collected.
os.environ.setdefault("ARIAOPS_HOST", "vrops.test.local")
os.environ.setdefault("ARIAOPS_USERNAME", "testuser")
os.environ.setdefault("ARIAOPS_PASSWORD", "testpass")


@pytest.fixture(autouse=True)
def reset_client():
    """Reset the module-level client, registry, and settings cache before each test."""
    client_module.reset_client_cache()
    client_module._client_override.set(None)
    # Reset skill registry singleton to avoid cross-test pollution.
    from ariaops_mcp.skills.registry import reset_registry
    reset_registry()
    # Clear tool registry cache so write-ops toggle is re-evaluated.
    import ariaops_mcp.server as server_module
    server_module._tool_defs = None
    server_module._tool_handlers = None
    clear_settings_cache()
    yield
    client_module.reset_client_cache()
    reset_registry()
    server_module._tool_defs = None
    server_module._tool_handlers = None
    clear_settings_cache()


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
