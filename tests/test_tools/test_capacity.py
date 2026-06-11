"""Tests for capacity tools."""

import json

import httpx
import pytest
import respx

from ariaops_mcp.tools._common import MAX_LIST_ITEMS, PAGE_SIZE_MAX, truncate_list_response
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


# ---------------------------------------------------------------------------
# PAGE_SIZE_MAX / pagination tests
# ---------------------------------------------------------------------------

def test_page_size_max_value():
    assert PAGE_SIZE_MAX == 200


def test_max_list_items_value():
    assert MAX_LIST_ITEMS == 50


def test_truncate_list_response_at_new_limit():
    data = {"items": list(range(60))}
    result = truncate_list_response(data, "items")
    assert len(result["items"]) == 50
    assert result["_truncated"] is True
    assert result["_truncatedAt"] == 50


def test_truncate_list_response_under_limit_not_truncated():
    data = {"items": list(range(50))}
    result = truncate_list_response(data, "items")
    assert len(result["items"]) == 50
    assert "_truncated" not in result


@pytest.mark.asyncio
async def test_get_capacity_overview_multi_page(handlers):
    """get_capacity_overview must paginate until all resources are collected."""
    page_calls: list[int] = []

    def resources_side_effect(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 0))
        page_size = int(request.url.params.get("pageSize", 50))
        page_calls.append(page)
        total = 250  # spans 2 full pages + 1 partial when pageSize=200; 2 pages when pageSize≥200
        start = page * page_size
        end = min(start + page_size, total)
        resources = [{"identifier": f"res-{i}"} for i in range(start, end)]
        return httpx.Response(
            200,
            json={
                "resourceList": resources,
                "pageInfo": {"totalCount": total, "page": page, "pageSize": page_size},
            },
        )

    stats_response = {"values": []}

    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/resources").mock(side_effect=resources_side_effect)
        respx.post(f"{BASE}/resources/stats/latest/query").mock(
            return_value=httpx.Response(200, json=stats_response)
        )

        result = await handlers["get_capacity_overview"]({"resourceKind": "ClusterComputeResource"})
        data = json.loads(result)

    assert data["resourceCount"] == 250
    # With PAGE_SIZE_MAX=200, page 0 fetches 200 resources, page 1 fetches 50 → done
    assert len(page_calls) == 2
    assert page_calls == [0, 1]


@pytest.mark.asyncio
async def test_get_capacity_overview_chunks_stats_query(handlers):
    """The latest-stats query is sent in PAGE_SIZE_MAX-sized id chunks and merged."""

    def resources_side_effect(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 0))
        page_size = int(request.url.params.get("pageSize", 50))
        total = 250
        start = page * page_size
        end = min(start + page_size, total)
        return httpx.Response(
            200,
            json={
                "resourceList": [{"identifier": f"res-{i}"} for i in range(start, end)],
                "pageInfo": {"totalCount": total, "page": page, "pageSize": page_size},
            },
        )

    stats_bodies: list[dict] = []

    def stats_side_effect(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        stats_bodies.append(body)
        values = [{"resourceId": r["resourceId"]} for r in body["resourceId"]]
        return httpx.Response(200, json={"values": values})

    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/resources").mock(side_effect=resources_side_effect)
        respx.post(f"{BASE}/resources/stats/latest/query").mock(side_effect=stats_side_effect)

        result = await handlers["get_capacity_overview"]({"resourceKind": "Datastore"})
        data = json.loads(result)

    assert len(stats_bodies) == 2
    assert len(stats_bodies[0]["resourceId"]) == PAGE_SIZE_MAX
    assert len(stats_bodies[1]["resourceId"]) == 250 - PAGE_SIZE_MAX
    assert data["resourceCount"] == 250
    # Chunked responses are merged back into a single values list.
    assert len(data["capacityStats"]["values"]) == 250


@pytest.mark.asyncio
async def test_get_capacity_overview_single_page_exact_boundary(handlers):
    """When totalCount == pageSize, only one request is made (no unnecessary second fetch)."""
    page_calls: list[int] = []

    def resources_side_effect(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 0))
        page_size = int(request.url.params.get("pageSize", 50))
        page_calls.append(page)
        resources = [{"identifier": f"res-{i}"} for i in range(page_size)]
        return httpx.Response(
            200,
            json={
                "resourceList": resources,
                "pageInfo": {"totalCount": page_size, "page": page, "pageSize": page_size},
            },
        )

    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/resources").mock(side_effect=resources_side_effect)
        respx.post(f"{BASE}/resources/stats/latest/query").mock(
            return_value=httpx.Response(200, json={"values": []})
        )

        result = await handlers["get_capacity_overview"]({})
        data = json.loads(result)

    assert len(page_calls) == 1
    assert data["resourceCount"] == PAGE_SIZE_MAX


@pytest.mark.asyncio
async def test_get_capacity_overview_uses_page_size_max(handlers):
    """Verify the pageSize query param sent to the API equals PAGE_SIZE_MAX."""
    sent_page_sizes: list[int] = []

    def resources_side_effect(request: httpx.Request) -> httpx.Response:
        sent_page_sizes.append(int(request.url.params.get("pageSize", 0)))
        return httpx.Response(
            200,
            json={"resourceList": [], "pageInfo": {"totalCount": 0}},
        )

    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/resources").mock(side_effect=resources_side_effect)

        await handlers["get_capacity_overview"]({})

    assert sent_page_sizes == [PAGE_SIZE_MAX]
