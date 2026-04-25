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


@pytest.mark.asyncio
async def test_get_capacity_remaining_missing_id(handlers):
    result = await handlers["get_capacity_remaining"]({})
    data = json.loads(result)
    assert "error" in data
    assert "id" in data["error"]


@pytest.mark.asyncio
async def test_get_capacity_overview_http_status_error(handlers):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/resources").mock(
            return_value=httpx.Response(503, json={"message": "Service unavailable"})
        )

        result = await handlers["get_capacity_overview"]({})
        data = json.loads(result)
        assert "error" in data
        assert data["status_code"] == 503


@pytest.mark.asyncio
async def test_get_capacity_forecast_success(handlers):
    # Mock historical data response
    historical_data = {
        "resourceList": [{
            "identifier": "test-resource-id",
            "data": [
                {"data": [100.0, 90.0, 80.0, 70.0, 60.0]},  # Decreasing trend
                {"data": [1000, 1001, 1002, 1003, 1004], "timestamps": [1000, 2000, 3000, 4000, 5000]}
            ]
        }]
    }
    
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.post(f"{BASE}/resources/stats/history/query").mock(return_value=httpx.Response(200, json=historical_data))

        result = await handlers["get_capacity_forecast"]({
            "id": "test-resource-id",
            "metric": "capacity|remainingCapacity",
            "days_ahead": 5
        })
        data = json.loads(result)

        assert data["resourceId"] == "test-resource-id"
        assert data["metric"] == "capacity|remainingCapacity"
        assert data["forecastPeriodDays"] == 5
        assert len(data["forecast"]) == 5
        assert "historicalStats" in data
        assert data["historicalStats"]["trend"] == "decreasing"
        # Check that forecast values are projected (should be decreasing based on historical trend)
        assert data["forecast"][0]["predictedValue"] < 60.0  # Last historical value was 60.0


@pytest.mark.asyncio
async def test_get_capacity_forecast_insufficient_data(handlers):
    # Mock insufficient historical data
    insufficient_data = {
        "resourceList": [{
            "identifier": "test-resource-id",
            "data": [{"data": [100.0]}]  # Only one data point
        }]
    }
    history_url = f"{BASE}/resources/stats/history/query"

    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.post(history_url).mock(
            return_value=httpx.Response(200, json=insufficient_data)
        )

        result = await handlers["get_capacity_forecast"]({
            "id": "test-resource-id",
            "metric": "capacity|remainingCapacity",
            "days_ahead": 5
        })
        data = json.loads(result)

        assert "error" in data
        assert "Insufficient historical data" in data["error"]


@pytest.mark.asyncio
async def test_get_capacity_forecast_missing_args(handlers):
    # Test missing required arguments
    result = await handlers["get_capacity_forecast"]({})
    data = json.loads(result)
    assert "error" in data
    assert "id" in data["error"] or "metric" in data["error"] or "days_ahead" in data["error"]


@pytest.mark.asyncio
async def test_get_trend_analysis_success(handlers):
    # Mock historical data with clear trend
    historical_data = {
        "resourceList": [{
            "identifier": "test-resource-id",
            "data": [
                {"data": [10.0, 20.0, 30.0, 40.0, 50.0]},  # Increasing trend
                {"data": [1000, 2000, 3000, 4000, 5000], "timestamps": [1000, 2000, 3000, 4000, 5000]}
            ]
        }]
    }
    
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.post(f"{BASE}/resources/stats/history/query").mock(return_value=httpx.Response(200, json=historical_data))

        result = await handlers["get_trend_analysis"]({
            "id": "test-resource-id",
            "metric": "mem|host_usable",
            "period_days": 30
        })
        data = json.loads(result)

        assert data["resourceId"] == "test-resource-id"
        assert data["metric"] == "mem|host_usable"
        assert data["dataPoints"] == 5
        assert data["trend"]["direction"] == "increasing"
        assert data["trend"]["slope"] > 0
        assert data["statistics"]["mean"] == 30.0
        assert data["statistics"]["min"] == 10.0
        assert data["statistics"]["max"] == 50.0


@pytest.mark.asyncio
async def test_get_trend_analysis_insufficient_data(handlers):
    # Mock insufficient historical data
    insufficient_data = {
        "resourceList": [{
            "identifier": "test-resource-id",
            "data": [{"data": [100.0]}]  # Only one data point
        }]
    }
    history_url = f"{BASE}/resources/stats/history/query"

    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.post(history_url).mock(
            return_value=httpx.Response(200, json=insufficient_data)
        )

        result = await handlers["get_trend_analysis"]({
            "id": "test-resource-id",
            "metric": "mem|host_usable",
            "period_days": 30
        })
        data = json.loads(result)

        assert "error" in data
        assert "Insufficient historical data" in data["error"]


@pytest.mark.asyncio
async def test_get_trend_analysis_missing_args(handlers):
    # Test missing required arguments
    result = await handlers["get_trend_analysis"]({})
    data = json.loads(result)
    assert "error" in data
    assert "id" in data["error"] or "metric" in data["error"]
