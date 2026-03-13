"""Tests for report tools."""

import json

import httpx
import pytest
import respx

from ariaops_mcp.tools.reports import tool_handlers
from tests.conftest import TOKEN_RESPONSE

BASE = "https://vrops.test.local/suite-api/api"

REPORT_DEFS = {
    "reportDefinitions": [
        {"id": "def-001", "name": "VM Capacity Report"},
        {"id": "def-002", "name": "Cluster Health Report"},
    ]
}

REPORTS_LIST = {
    "reports": [
        {"id": "rep-001", "name": "VM Capacity Report - Jan"},
    ]
}


@pytest.fixture
def handlers(mock_env):
    return tool_handlers()


@pytest.mark.asyncio
async def test_list_report_definitions(handlers):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/reportdefinitions").mock(return_value=httpx.Response(200, json=REPORT_DEFS))

        result = await handlers["list_report_definitions"]({})
        data = json.loads(result)
        assert "reportDefinitions" in data
        assert data["reportDefinitions"][0]["id"] == "def-001"


@pytest.mark.asyncio
async def test_list_reports(handlers):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/reports").mock(return_value=httpx.Response(200, json=REPORTS_LIST))

        result = await handlers["list_reports"]({})
        data = json.loads(result)
        assert "reports" in data
        assert data["reports"][0]["id"] == "rep-001"


@pytest.mark.asyncio
async def test_download_report(handlers):
    raw_content = b"PDF content here"
    import base64

    expected_b64 = base64.b64encode(raw_content).decode("utf-8")

    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/reports/rep-001/download").mock(return_value=httpx.Response(200, content=raw_content))

        result = await handlers["download_report"]({"id": "rep-001"})
        data = json.loads(result)
        assert data["reportId"] == "rep-001"
        assert data["encoding"] == "base64"
        assert data["content"] == expected_b64


@pytest.mark.asyncio
async def test_list_report_definitions_missing_id_on_get(handlers):
    result = await handlers["get_report_definition"]({})
    data = json.loads(result)
    assert "error" in data
    assert "id" in data["error"]


@pytest.mark.asyncio
async def test_list_reports_http_status_error(handlers):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/reports").mock(return_value=httpx.Response(503, json={"message": "Service unavailable"}))

        result = await handlers["list_reports"]({})
        data = json.loads(result)
        assert "error" in data
        assert data["status_code"] == 503
