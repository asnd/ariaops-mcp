"""Tests for metrics tools."""

import json

import httpx
import pytest
import respx

from ariaops_mcp.tools.metrics import tool_handlers
from tests.conftest import TOKEN_RESPONSE

BASE = "https://vrops.test.local/suite-api/api"

STATS_RESPONSE = {
    "values": [
        {
            "statKey": {"key": "cpu|usage_average"},
            "timestamps": [1700000000000],
            "data": [12.5],
        }
    ]
}

LATEST_STATS_RESPONSE = {
    "values": [
        {
            "statKey": {"key": "cpu|usage_average"},
            "timestamps": [1700000000000],
            "data": [8.3],
        }
    ]
}

BULK_STATS_RESPONSE = {
    "values": [
        {
            "resourceId": {"resourceId": "vm-001"},
            "stat-list": {"stat": []},
        }
    ]
}


@pytest.fixture
def handlers(mock_env):
    return tool_handlers()


@pytest.mark.asyncio
async def test_get_resource_stats(handlers):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/resources/vm-001/stats").mock(return_value=httpx.Response(200, json=STATS_RESPONSE))

        result = await handlers["get_resource_stats"]({"id": "vm-001", "statKey": "cpu|usage_average"})
        data = json.loads(result)
        assert "values" in data
        assert data["values"][0]["data"][0] == 12.5


@pytest.mark.asyncio
async def test_get_latest_stats(handlers):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/resources/vm-001/stats/latest").mock(
            return_value=httpx.Response(200, json=LATEST_STATS_RESPONSE)
        )

        result = await handlers["get_latest_stats"]({"id": "vm-001"})
        data = json.loads(result)
        assert "values" in data
        assert data["values"][0]["data"][0] == 8.3


@pytest.mark.asyncio
async def test_query_stats(handlers):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.post(f"{BASE}/resources/stats/query").mock(return_value=httpx.Response(200, json=BULK_STATS_RESPONSE))

        result = await handlers["query_stats"]({"resourceIds": ["vm-001"], "statKeys": ["cpu|usage_average"]})
        data = json.loads(result)
        assert "values" in data


@pytest.mark.asyncio
async def test_get_resource_stats_missing_id(handlers):
    result = await handlers["get_resource_stats"]({})
    data = json.loads(result)
    assert "error" in data
    assert "id" in data["error"]


@pytest.mark.asyncio
async def test_get_resource_stats_http_status_error(handlers):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/resources/vm-001/stats").mock(
            return_value=httpx.Response(404, json={"message": "Not found"})
        )

        result = await handlers["get_resource_stats"]({"id": "vm-001"})
        data = json.loads(result)
        assert "error" in data
        assert data["status_code"] == 404
