"""MCP server setup and tool registration."""

import json
from typing import cast

import mcp.types as types
from mcp.server import Server
from pydantic import AnyUrl

from ariaops_mcp.config import write_operations_enabled
from ariaops_mcp.tools import alerts, capacity, discovery, metrics, reports, resources
from ariaops_mcp.tools import write_ops as write_operations_tools

TOOL_MODULES = [resources, alerts, metrics, capacity, reports, discovery]
WRITE_TOOL_MODULES = [write_operations_tools]


def _is_missing_required_argument(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return len(value) == 0 or all(_is_missing_required_argument(item) for item in value)
    return False


def _missing_required_arguments(tool: types.Tool, arguments: dict) -> list[str]:
    required = tool.inputSchema.get("required", [])
    return [key for key in required if _is_missing_required_argument(arguments.get(key))]


def _build_registry(include_write_operations: bool = False) -> tuple[list[types.Tool], dict]:
    defs: list[types.Tool] = []
    handlers: dict = {}
    modules = TOOL_MODULES + (WRITE_TOOL_MODULES if include_write_operations else [])
    for mod in modules:
        defs.extend(mod.tool_definitions())
        handlers.update(mod.tool_handlers())
    return defs, handlers


def _build_tool_defs_by_name(tool_defs: list[types.Tool]) -> dict[str, types.Tool]:
    by_name: dict[str, types.Tool] = {}
    for tool in tool_defs:
        if tool.name in by_name:
            raise ValueError(f"Duplicate tool definition: {tool.name}")
        by_name[tool.name] = tool
    return by_name


def create_server() -> Server:
    tool_defs, tool_handlers = _build_registry(include_write_operations=write_operations_enabled())
    tool_defs_by_name = _build_tool_defs_by_name(tool_defs)
    server = Server("ariaops-mcp")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return tool_defs

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
        handler = tool_handlers.get(name)
        if not handler:
            raise ValueError(f"Unknown tool: {name}")
        tool = tool_defs_by_name.get(name)
        if not tool:
            raise ValueError(f"Tool metadata not found: {name}")

        if arguments is not None and not isinstance(arguments, dict):
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "error": "Invalid tool arguments payload",
                            "detail": "Expected a JSON object for tool arguments.",
                        }
                    ),
                )
            ]

        parsed_args = arguments or {}
        missing_required = _missing_required_arguments(tool, parsed_args)
        if missing_required:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "error": f"Missing required argument(s): {', '.join(missing_required)}",
                            "missing": missing_required,
                            "next_step": "Ask the user for the missing value(s) and retry this tool call.",
                        }
                    ),
                )
            ]

        result = await handler(parsed_args)
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
