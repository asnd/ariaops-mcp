"""Integration tests for response-shaping strategies through tool handlers."""

import json

import httpx
import pytest
import respx

from ariaops_mcp.tools.alerts import tool_handlers as alert_handlers
from ariaops_mcp.tools.resources import tool_handlers as resource_handlers
from tests.conftest import TOKEN_RESPONSE

BASE = "https://vrops.test.local/suite-api/api"

LARGE_RESOURCE_LIST = {
    "pageInfo": {"totalCount": 200, "page": 0, "pageSize": 50},
    "resourceList": [
        {
            "identifier": f"vm-{i:03d}",
            "resourceKey": {"name": f"VM-{i}", "adapterKindKey": "VMWARE", "resourceKindKey": "VirtualMachine"},
            "badges": {"status": "GREEN"},
            "links": [{"href": f"/resources/vm-{i:03d}"}],
        }
        for i in range(50)
    ],
}


@pytest.fixture
def res_handlers(mock_env):
    return resource_handlers()


@pytest.fixture
def alrt_handlers(mock_env):
    return alert_handlers()


# ---------------------------------------------------------------------------
# Strategy 1 – Enhanced pagination hints via list_resources
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_resources_includes_pagination_hints(res_handlers):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/resources").mock(return_value=httpx.Response(200, json=LARGE_RESOURCE_LIST))

        result = await res_handlers["list_resources"]({})
        data = json.loads(result)

        assert data["_totalCount"] == 200
        assert data["_nextPage"] == 1
        assert "page=1" in data["_hint"]


# ---------------------------------------------------------------------------
# Strategy 2 – Field filtering via list_resources
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_resources_field_filtering(res_handlers):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/resources").mock(return_value=httpx.Response(200, json=LARGE_RESOURCE_LIST))

        result = await res_handlers["list_resources"]({"fields": ["identifier"]})
        data = json.loads(result)

        for item in data["resourceList"]:
            assert list(item.keys()) == ["identifier"]


# ---------------------------------------------------------------------------
# Strategy 3 – Summary mode via list_resources
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_resources_summary_mode(res_handlers):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/resources").mock(return_value=httpx.Response(200, json=LARGE_RESOURCE_LIST))

        result = await res_handlers["list_resources"]({"summaryOnly": True})
        data = json.loads(result)

        for item in data["resourceList"]:
            assert set(item.keys()) == {"identifier", "resourceKey"}
            assert "badges" not in item
            assert "links" not in item


# ---------------------------------------------------------------------------
# Strategy 2 + alerts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_alerts_field_filtering(alrt_handlers):
    alert_list = {
        "pageInfo": {"totalCount": 2, "page": 0, "pageSize": 50},
        "alerts": [
            {"id": "a1", "status": "ACTIVE", "criticality": "CRITICAL", "extra": "verbose"},
            {"id": "a2", "status": "CANCELLED", "criticality": "WARNING", "extra": "verbose"},
        ],
    }
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/alerts").mock(return_value=httpx.Response(200, json=alert_list))

        result = await alrt_handlers["list_alerts"]({"fields": ["id", "status"]})
        data = json.loads(result)

        assert data["alerts"] == [{"id": "a1", "status": "ACTIVE"}, {"id": "a2", "status": "CANCELLED"}]


# ---------------------------------------------------------------------------
# Strategy 3 + alerts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_alerts_summary_mode(alrt_handlers):
    alert_list = {
        "pageInfo": {"totalCount": 1, "page": 0, "pageSize": 50},
        "alerts": [
            {
                "id": "a1",
                "alertDefinitionName": "CPU",
                "status": "ACTIVE",
                "criticality": "CRITICAL",
                "resourceId": "r1",
                "startTimeUTC": 1234567890,
                "updateTimeUTC": 1234567891,
                "alertDefinitionId": "def-001",
            }
        ],
    }
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/alerts").mock(return_value=httpx.Response(200, json=alert_list))

        result = await alrt_handlers["list_alerts"]({"summaryOnly": True})
        data = json.loads(result)

        alert = data["alerts"][0]
        assert set(alert.keys()) == {"id", "alertDefinitionName", "status", "criticality", "resourceId"}
