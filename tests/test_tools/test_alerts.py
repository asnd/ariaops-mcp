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


@pytest.mark.asyncio
async def test_get_alert_missing_id(handlers):
    result = await handlers["get_alert"]({})
    data = json.loads(result)
    assert "error" in data
    assert "id" in data["error"]


@pytest.mark.asyncio
async def test_list_alerts_http_status_error(handlers):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/alerts").mock(
            return_value=httpx.Response(503, json={"message": "Service unavailable"})
        )

        result = await handlers["list_alerts"]({})
        data = json.loads(result)
        assert "error" in data
        assert data["status_code"] == 503


@pytest.mark.asyncio
async def test_query_alerts(handlers):
    alerts_resp = {"alerts": [{"id": "alert-002", "status": "ACTIVE"}]}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.post(f"{BASE}/alerts/query").mock(return_value=httpx.Response(200, json=alerts_resp))

        result = await handlers["query_alerts"]({"alertStatus": ["ACTIVE"]})
        data = json.loads(result)
        assert data["alerts"][0]["id"] == "alert-002"


@pytest.mark.asyncio
async def test_get_alert_notes(handlers):
    notes = {"notes": [{"id": "note-001", "note": "Investigating"}]}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/alerts/alert-001/notes").mock(return_value=httpx.Response(200, json=notes))

        result = await handlers["get_alert_notes"]({"id": "alert-001"})
        data = json.loads(result)
        assert data["notes"][0]["note"] == "Investigating"


@pytest.mark.asyncio
async def test_get_alert_definition(handlers):
    adef = {"id": "def-001", "name": "CPU Contention", "adapterKindKey": "VMWARE"}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/alertdefinitions/def-001").mock(return_value=httpx.Response(200, json=adef))

        result = await handlers["get_alert_definition"]({"id": "def-001"})
        data = json.loads(result)
        assert data["id"] == "def-001"


@pytest.mark.asyncio
async def test_get_contributing_symptoms(handlers):
    symptoms = {"symptomDefinitions": [{"id": "sym-001", "name": "High CPU"}]}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/alerts/contributingsymptoms").mock(return_value=httpx.Response(200, json=symptoms))

        result = await handlers["get_contributing_symptoms"]({})
        data = json.loads(result)
        assert data["symptomDefinitions"][0]["id"] == "sym-001"
