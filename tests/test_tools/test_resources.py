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


@pytest.mark.asyncio
async def test_query_resources(handlers):
    resource_list = {
        "resourceList": [
            {"identifier": "vm-002", "resourceKey": {"name": "TestVM2", "adapterKindKey": "VMWARE"}}
        ]
    }
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.post(f"{BASE}/resources/query").mock(return_value=httpx.Response(200, json=resource_list))

        result = await handlers["query_resources"]({"adapterKind": "VMWARE", "resourceKind": "VirtualMachine"})
        data = json.loads(result)
        assert data["resourceList"][0]["identifier"] == "vm-002"


@pytest.mark.asyncio
async def test_get_resource_properties(handlers):
    props = {"property": [{"name": "summary|guest", "value": "Ubuntu 22.04"}]}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/resources/vm-001/properties").mock(return_value=httpx.Response(200, json=props))

        result = await handlers["get_resource_properties"]({"id": "vm-001"})
        data = json.loads(result)
        assert "property" in data


@pytest.mark.asyncio
async def test_get_resource_relationships(handlers):
    rels = {"resourceRelations": [{"resourceId": "host-001", "relationshipType": "CHILD"}]}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/resources/vm-001/relationships").mock(return_value=httpx.Response(200, json=rels))

        result = await handlers["get_resource_relationships"]({"id": "vm-001"})
        data = json.loads(result)
        assert "resourceRelations" in data


@pytest.mark.asyncio
async def test_get_resource_relationships_invalid_type(handlers):
    result = await handlers["get_resource_relationships"]({"id": "vm-001", "relationshipType": "INVALID"})
    data = json.loads(result)
    assert "error" in data
    assert "Invalid relationshipType" in data["error"]


@pytest.mark.asyncio
async def test_list_resource_kinds(handlers):
    kinds = {"resourceKind": [{"resourceKindKey": "VirtualMachine", "adapterKindKey": "VMWARE"}]}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/adapterkinds/VMWARE/resourcekinds").mock(return_value=httpx.Response(200, json=kinds))

        result = await handlers["list_resource_kinds"]({"adapterKindKey": "VMWARE"})
        data = json.loads(result)
        assert "resourceKind" in data


@pytest.mark.asyncio
async def test_list_resource_groups(handlers):
    groups = {"resourceGroups": [{"id": "grp-001", "name": "Production VMs"}]}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/resources/groups").mock(return_value=httpx.Response(200, json=groups))

        result = await handlers["list_resource_groups"]({})
        data = json.loads(result)
        assert "resourceGroups" in data


@pytest.mark.asyncio
async def test_get_resource_group_members(handlers):
    members = {"members": [{"identifier": "vm-001"}, {"identifier": "vm-002"}]}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        respx.get(f"{BASE}/resources/groups/grp-001/members").mock(return_value=httpx.Response(200, json=members))

        result = await handlers["get_resource_group_members"]({"groupId": "grp-001"})
        data = json.loads(result)
        assert "members" in data
