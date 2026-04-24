"""Tests for write-operation tools."""

import json

import httpx
import pytest
import respx

from ariaops_mcp.tools.write_ops import tool_handlers
from tests.conftest import TOKEN_RESPONSE

BASE = "https://vrops.test.local/suite-api/api"


@pytest.fixture
def handlers(mock_env):
    return tool_handlers()


@pytest.mark.asyncio
async def test_write_tools_disabled_by_default(handlers):
    result = await handlers["add_alert_note"]({"id": "alert-001", "note": "hello"})
    data = json.loads(result)
    assert "disabled" in data["error"].lower()
    assert "ARIAOPS_ENABLE_WRITE_OPERATIONS" in data["next_step"]


@pytest.mark.asyncio
async def test_add_alert_note_when_enabled(mock_env, monkeypatch):
    monkeypatch.setenv("ARIAOPS_ENABLE_WRITE_OPERATIONS", "true")
    handlers = tool_handlers()
    expected = {"id": "note-1", "message": "created"}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.post(f"{BASE}/alerts/alert-001/notes").mock(return_value=httpx.Response(200, json=expected))

        result = await handlers["add_alert_note"]({"id": "alert-001", "note": "Investigating"})
        data = json.loads(result)
        assert data["id"] == "note-1"


@pytest.mark.asyncio
async def test_set_alert_status_when_enabled(mock_env, monkeypatch):
    monkeypatch.setenv("ARIAOPS_ENABLE_WRITE_OPERATIONS", "true")
    handlers = tool_handlers()
    expected = {"id": "alert-001", "status": "SUSPENDED"}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.post(f"{BASE}/alerts/alert-001/suspend").mock(return_value=httpx.Response(200, json=expected))

        result = await handlers["set_alert_status"]({"id": "alert-001", "action": "suspend"})
        data = json.loads(result)
        assert data["status"] == "SUSPENDED"


@pytest.mark.asyncio
async def test_add_alert_note_rejects_control_characters(mock_env, monkeypatch):
    monkeypatch.setenv("ARIAOPS_ENABLE_WRITE_OPERATIONS", "true")
    handlers = tool_handlers()
    result = await handlers["add_alert_note"]({"id": "alert-001", "note": "bad\x00note"})
    data = json.loads(result)
    assert "control characters" in data["error"]


@pytest.mark.asyncio
async def test_set_alert_status_rejects_invalid_action(mock_env, monkeypatch):
    monkeypatch.setenv("ARIAOPS_ENABLE_WRITE_OPERATIONS", "true")
    handlers = tool_handlers()
    result = await handlers["set_alert_status"]({"id": "alert-001", "action": "DELETE"})
    data = json.loads(result)
    assert "Invalid action" in data["error"]


@pytest.mark.asyncio
async def test_add_alert_note_encodes_alert_id(mock_env, monkeypatch):
    monkeypatch.setenv("ARIAOPS_ENABLE_WRITE_OPERATIONS", "true")
    handlers = tool_handlers()
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        encoded = respx.post(f"{BASE}/alerts/alert%2F001/notes").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        result = await handlers["add_alert_note"]({"id": "alert/001", "note": "encoded"})
        data = json.loads(result)
        assert data["ok"] is True
        assert encoded.call_count == 1
