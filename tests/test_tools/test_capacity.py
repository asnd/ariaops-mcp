"""Tests for capacity tools."""

import json

import httpx
import pytest
import respx

from ariaops_mcp.tools.capacity import tool_handlers
from tests.conftest import TOKEN_RESPONSE

BASE = "https://vrops.test.local/suite-api/api"


@pytest.fixture
def handlers(mock_env):
    return tool_handlers()


@pytest.mark.asyncio
async def test_get_capacity_remaining_partial_stat_failures(handlers):
    attempts: dict[str, int] = {}

    def latest_stats_response(request: httpx.Request) -> httpx.Response:
        stat_key = request.url.params.get("statKey", "")
        attempts[stat_key] = attempts.get(stat_key, 0) + 1

        if stat_key == "capacity|timeRemaining":
            return httpx.Response(503, json={"error": "temporary"})

        if stat_key == "capacity|remainingCapacity":
            return httpx.Response(200, json={"values": [{"data": [12.5]}]})

        return httpx.Response(200, json={"values": []})

    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        # One stat succeeds, one repeatedly fails; handler should continue.
        respx.get(f"{BASE}/resources/cluster-001/stats/latest").mock(side_effect=latest_stats_response)

        result = await handlers["get_capacity_remaining"]({"id": "cluster-001"})
        data = json.loads(result)

        assert data["resourceId"] == "cluster-001"
        assert data["capacityStats"]
        assert "capacity|remainingCapacity" in data["capacityStats"]
        assert "capacity|timeRemaining" not in data["capacityStats"]
        assert attempts["capacity|timeRemaining"] == 4


@pytest.mark.asyncio
async def test_get_capacity_overview_no_resources(handlers):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/resources").mock(return_value=httpx.Response(200, json={"resourceList": []}))

        result = await handlers["get_capacity_overview"]({"resourceKind": "ClusterComputeResource"})
        data = json.loads(result)

        assert data["message"] == "No resources found"
        assert data["resourceKind"] == "ClusterComputeResource"
