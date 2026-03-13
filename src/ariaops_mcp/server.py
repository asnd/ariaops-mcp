"""MCP server setup and tool registration."""

import mcp.types as types
from mcp.server import Server

from ariaops_mcp.tools import alerts, capacity, discovery, metrics, reports, resources


def create_server() -> Server:
    server = Server("ariaops-mcp")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return (
            resources.tool_definitions()
            + alerts.tool_definitions()
            + metrics.tool_definitions()
            + capacity.tool_definitions()
            + reports.tool_definitions()
            + discovery.tool_definitions()
        )

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        handlers = {
            **resources.tool_handlers(),
            **alerts.tool_handlers(),
            **metrics.tool_handlers(),
            **capacity.tool_handlers(),
            **reports.tool_handlers(),
            **discovery.tool_handlers(),
        }
        handler = handlers.get(name)
        if not handler:
            raise ValueError(f"Unknown tool: {name}")
        result = await handler(arguments)
        return [types.TextContent(type="text", text=result)]

    return server
