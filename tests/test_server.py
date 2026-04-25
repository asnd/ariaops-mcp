"""Tests for MCP server creation and behavior."""

import json

import httpx
import pytest
import respx
from mcp.types import (
    CallToolRequest,
    ListResourcesRequest,
    ListToolsRequest,
    ReadResourceRequest,
)

from ariaops_mcp.server import create_server
from tests.conftest import TOKEN_RESPONSE

BASE = "https://vrops.test.local/suite-api/api"


@pytest.mark.asyncio
async def test_list_tools_readonly(mock_env):
    server = create_server()
    result = await server.request_handlers[ListToolsRequest](
        ListToolsRequest(method="tools/list", params=None)
    )
    tools = result.root.tools
    tool_names = {t.name for t in tools}
    assert "list_resources" in tool_names
    assert "list_alerts" in tool_names
    assert "get_resource_stats" in tool_names
    assert "list_report_definitions" in tool_names
    assert "modify_alerts" not in tool_names
    assert "delete_resources" not in tool_names


@pytest.mark.asyncio
async def test_list_resources(mock_env):
    server = create_server()
    result = await server.request_handlers[ListResourcesRequest](
        ListResourcesRequest(method="resources/list", params=None)
    )
    resources = result.root.resources
    uris = {str(r.uri) for r in resources}
    assert "ariaops://version" in uris
    assert "ariaops://adapter-kinds" in uris


@pytest.mark.asyncio
async def test_read_resource_version(mock_env):
    version_data = {"releaseName": "8.18.0", "buildNumber": "12345678"}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/versions/current").mock(
            return_value=httpx.Response(200, json=version_data)
        )

        server = create_server()
        result = await server.request_handlers[ReadResourceRequest](
            ReadResourceRequest(method="resources/read", params={"uri": "ariaops://version"})
        )

        contents = result.root.contents
        assert len(contents) == 1
        data = json.loads(contents[0].text)
        assert data["releaseName"] == "8.18.0"


@pytest.mark.asyncio
async def test_read_resource_adapter_kinds(mock_env):
    adapter_data = {"adapterKindList": [{"key": "VMWARE", "name": "VMware Adapter"}]}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/adapterkinds").mock(
            return_value=httpx.Response(200, json=adapter_data)
        )

        server = create_server()
        result = await server.request_handlers[ReadResourceRequest](
            ReadResourceRequest(
                method="resources/read", params={"uri": "ariaops://adapter-kinds"}
            )
        )

        contents = result.root.contents
        assert len(contents) == 1
        data = json.loads(contents[0].text)
        assert data["adapterKindList"][0]["key"] == "VMWARE"


@pytest.mark.asyncio
async def test_read_resource_error(mock_env):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/versions/current").mock(
            return_value=httpx.Response(503, json={"message": "down"})
        )

        server = create_server()
        result = await server.request_handlers[ReadResourceRequest](
            ReadResourceRequest(method="resources/read", params={"uri": "ariaops://version"})
        )

        contents = result.root.contents
        assert len(contents) == 1
        data = json.loads(contents[0].text)
        assert "error" in data


@pytest.mark.asyncio
async def test_read_resource_unknown_uri(mock_env):
    server = create_server()
    with pytest.raises(ValueError, match="Unknown resource URI"):
        await server.request_handlers[ReadResourceRequest](
            ReadResourceRequest(method="resources/read", params={"uri": "ariaops://unknown"})
        )


@pytest.mark.asyncio
async def test_call_tool_unknown(mock_env):
    server = create_server()
    result = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call", params={"name": "nonexistent_tool", "arguments": {}}
        )
    )
    assert result.root.isError is True
    assert "Unknown tool" in result.root.content[0].text


@pytest.mark.asyncio
async def test_call_tool_get_version(mock_env):
    version_data = {"releaseName": "8.18.0", "buildNumber": "12345678"}
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/versions/current").mock(
            return_value=httpx.Response(200, json=version_data)
        )

        server = create_server()
        result = await server.request_handlers[CallToolRequest](
            CallToolRequest(
                method="tools/call", params={"name": "get_version", "arguments": {}}
            )
        )

        content = result.root.content
        assert len(content) == 1
        data = json.loads(content[0].text)
        assert data["releaseName"] == "8.18.0"
