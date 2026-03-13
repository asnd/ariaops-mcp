"""Tests for alert tools."""

import json

import httpx
import pytest
import respx

from ariaops_mcp.tools.alerts import tool_handlers
from tests.conftest import TOKEN_RESPONSE

BASE = "https://vrops.test.local/suite-api/api"

ALERT_LIST = {
    "pageInfo": {"totalCount": 1, "page": 0, "pageSize": 50},
    "alerts": [
        {
            "id": "alert-001",
            "status": "ACTIVE",
            "criticality": "CRITICAL",
            "alertDefinitionName": "CPU Contention",
        }
    ],
}


@pytest.fixture
def handlers(mock_env):
    return tool_handlers()


@pytest.mark.asyncio
async def test_list_alerts(handlers):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/alerts").mock(return_value=httpx.Response(200, json=ALERT_LIST))

        result = await handlers["list_alerts"]({})
        data = json.loads(result)
        assert data["alerts"][0]["id"] == "alert-001"
        assert data["alerts"][0]["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_get_alert(handlers):
    alert = {"id": "alert-001", "status": "ACTIVE", "criticality": "CRITICAL"}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/alerts/alert-001").mock(return_value=httpx.Response(200, json=alert))

        result = await handlers["get_alert"]({"id": "alert-001"})
        data = json.loads(result)
        assert data["id"] == "alert-001"


@pytest.mark.asyncio
async def test_list_alert_definitions(handlers):
    defs = {"alertDefinitions": [{"id": "def-001", "name": "CPU Contention"}]}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/alertdefinitions").mock(return_value=httpx.Response(200, json=defs))

        result = await handlers["list_alert_definitions"]({})
        data = json.loads(result)
        assert data["alertDefinitions"][0]["name"] == "CPU Contention"
