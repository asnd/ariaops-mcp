"""Tests for discovery tools."""

import json

import httpx
import pytest
import respx

from ariaops_mcp.tools.discovery import tool_handlers
from tests.conftest import TOKEN_RESPONSE

BASE = "https://vrops.test.local/suite-api/api"


@pytest.fixture
def handlers(mock_env):
    return tool_handlers()


@pytest.mark.asyncio
async def test_get_version(handlers):
    version_resp = {"releaseName": "8.18.0", "buildNumber": "12345678"}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/versions/current").mock(return_value=httpx.Response(200, json=version_resp))

        result = await handlers["get_version"]({})
        data = json.loads(result)
        assert data["releaseName"] == "8.18.0"


@pytest.mark.asyncio
async def test_list_collectors(handlers):
    collectors_resp = {"collector": [{"id": "col-001", "name": "Default Collector"}]}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/collectors").mock(return_value=httpx.Response(200, json=collectors_resp))

        result = await handlers["list_collectors"]({})
        data = json.loads(result)
        assert data["collector"][0]["id"] == "col-001"


@pytest.mark.asyncio
async def test_get_version_http_status_error(handlers):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/versions/current").mock(
            return_value=httpx.Response(503, json={"message": "Service unavailable"})
        )

        result = await handlers["get_version"]({})
        data = json.loads(result)
        assert "error" in data
        assert data["status_code"] == 503


@pytest.mark.asyncio
async def test_list_symptoms(handlers):
    symptoms = {"symptomDefinitions": [{"id": "sym-001", "name": "High CPU Usage"}]}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/symptomdefinitions").mock(return_value=httpx.Response(200, json=symptoms))

        result = await handlers["list_symptoms"]({})
        data = json.loads(result)
        assert "symptomDefinitions" in data


@pytest.mark.asyncio
async def test_list_recommendations(handlers):
    recs = {"recommendations": [{"id": "rec-001", "name": "Right-size VM"}]}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/recommendations").mock(return_value=httpx.Response(200, json=recs))

        result = await handlers["list_recommendations"]({})
        data = json.loads(result)
        assert "recommendations" in data


@pytest.mark.asyncio
async def test_list_supermetrics(handlers):
    smetrics = {"superMetrics": [{"id": "sm-001", "name": "Custom CPU Ratio"}]}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/supermetrics").mock(return_value=httpx.Response(200, json=smetrics))

        result = await handlers["list_supermetrics"]({})
        data = json.loads(result)
        assert data["superMetrics"][0]["id"] == "sm-001"
