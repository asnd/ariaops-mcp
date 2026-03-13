"""MCP server setup and tool registration."""

import json
from typing import cast

import mcp.types as types
from mcp.server import Server
from pydantic import AnyUrl

from ariaops_mcp.tools import alerts, capacity, discovery, metrics, reports, resources

TOOL_MODULES = [resources, alerts, metrics, capacity, reports, discovery]


def _build_registry() -> tuple[list[types.Tool], dict]:
    defs: list[types.Tool] = []
    handlers: dict = {}
    for mod in TOOL_MODULES:
        defs.extend(mod.tool_definitions())
        handlers.update(mod.tool_handlers())
    return defs, handlers


_TOOL_DEFS, _TOOL_HANDLERS = _build_registry()


def create_server() -> Server:
    server = Server("ariaops-mcp")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return _TOOL_DEFS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
        handler = _TOOL_HANDLERS.get(name)
        if not handler:
            raise ValueError(f"Unknown tool: {name}")
        result = await handler(arguments or {})
        return [types.TextContent(type="text", text=result)]

    @server.list_resources()
    async def list_resources() -> list[types.Resource]:
        return [
            types.Resource(
                uri=cast(AnyUrl, "ariaops://version"),
                name="Aria Operations Version",
                description="Current Aria Operations version and deployment info",
                mimeType="application/json",
            ),
            types.Resource(
                uri=cast(AnyUrl, "ariaops://adapter-kinds"),
                name="Aria Operations Adapter Kinds",
                description="All adapter kinds registered in Aria Operations",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: AnyUrl) -> str:
        from ariaops_mcp.client import get_client

        uri_str = str(uri)
        if uri_str == "ariaops://version":
            try:
                data = await get_client().get("/versions/current")
                return json.dumps(data, indent=2)
            except Exception as e:
                return json.dumps({"error": str(e)})
        elif uri_str == "ariaops://adapter-kinds":
            try:
                data = await get_client().get("/adapterkinds")
                return json.dumps(data, indent=2)
            except Exception as e:
                return json.dumps({"error": str(e)})
        else:
            raise ValueError(f"Unknown resource URI: {uri_str}")

    return server
