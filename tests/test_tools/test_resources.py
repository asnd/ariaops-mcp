"""Tests for resource tools."""

import json

import httpx
import pytest
import respx

from ariaops_mcp.tools.resources import tool_handlers
from tests.conftest import TOKEN_RESPONSE

BASE = "https://vrops.test.local/suite-api/api"

RESOURCE_LIST = {
    "pageInfo": {"totalCount": 1, "page": 0, "pageSize": 50},
    "resourceList": [
        {
            "identifier": "vm-001",
            "resourceKey": {"name": "TestVM", "adapterKindKey": "VMWARE", "resourceKindKey": "VirtualMachine"},
        }
    ],
}


@pytest.fixture
def handlers(mock_env):
    return tool_handlers()


@pytest.mark.asyncio
async def test_list_resources(handlers):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/resources").mock(return_value=httpx.Response(200, json=RESOURCE_LIST))

        result = await handlers["list_resources"]({})
        data = json.loads(result)
        assert data["resourceList"][0]["identifier"] == "vm-001"


@pytest.mark.asyncio
async def test_get_resource(handlers):
    resource = {"identifier": "vm-001", "resourceKey": {"name": "TestVM"}}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/resources/vm-001").mock(return_value=httpx.Response(200, json=resource))

        result = await handlers["get_resource"]({"id": "vm-001"})
        data = json.loads(result)
        assert data["identifier"] == "vm-001"


@pytest.mark.asyncio
async def test_list_adapter_kinds(handlers):
    adapter_kinds = {"adapterKindList": [{"key": "VMWARE", "name": "VMware Adapter"}]}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/adapterkinds").mock(return_value=httpx.Response(200, json=adapter_kinds))

        result = await handlers["list_adapter_kinds"]({})
        data = json.loads(result)
        assert data["adapterKindList"][0]["key"] == "VMWARE"


@pytest.mark.asyncio
async def test_get_resource_missing_id(handlers):
    result = await handlers["get_resource"]({})
    data = json.loads(result)
    assert "error" in data
    assert "id" in data["error"]


@pytest.mark.asyncio
async def test_get_resource_http_status_error(handlers):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/resources/missing-vm").mock(
            return_value=httpx.Response(404, json={"message": "Not found"})
        )

        result = await handlers["get_resource"]({"id": "missing-vm"})
        data = json.loads(result)
        assert "error" in data
        assert data["status_code"] == 404
