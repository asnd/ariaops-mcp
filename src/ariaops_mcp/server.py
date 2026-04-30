"""MCP server setup and tool registration."""

import json
import logging
import time
from typing import cast

import mcp.types as types
from mcp.server import Server
from pydantic import AnyUrl

from ariaops_mcp.circuit_breaker import CircuitOpenError
from ariaops_mcp.config import get_settings
from ariaops_mcp.logging_config import new_correlation_id
from ariaops_mcp.tools import alerts, capacity, discovery, metrics, reports, resources, write_ops

logger = logging.getLogger(__name__)

READ_ONLY_MODULES = [resources, alerts, metrics, capacity, reports, discovery]


def _build_registry() -> tuple[list[types.Tool], dict]:
    defs: list[types.Tool] = []
    handlers: dict = {}
    for mod in READ_ONLY_MODULES:
        defs.extend(mod.tool_definitions())
        handlers.update(mod.tool_handlers())
    if get_settings().enable_write_operations:
        defs.extend(write_ops.tool_definitions())
        handlers.update(write_ops.tool_handlers())
    return defs, handlers


_TOOL_DEFS, _TOOL_HANDLERS = _build_registry()


def create_server() -> Server:
    server = Server("ariaops-mcp")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return _TOOL_DEFS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
        cid = new_correlation_id()
        start = time.monotonic()
        handler = _TOOL_HANDLERS.get(name)
        if not handler:
            raise ValueError(f"Unknown tool: {name}")

        try:
            result = await handler(arguments or {})
        except CircuitOpenError as e:
            result = json.dumps({
                "error": "Service unavailable",
                "detail": str(e),
                "retry_after": e.retry_after,
                "correlation_id": cid,
            })
        except TimeoutError:
            result = json.dumps({
                "error": "Request deadline exceeded",
                "detail": f"Total time exceeded {get_settings().request_deadline}s including retries",
                "correlation_id": cid,
            })

        duration_ms = (time.monotonic() - start) * 1000
        logger.info(
            "tool_call: %s [%s] %.0fms",
            name,
            cid,
            duration_ms,
            extra={"event": "tool_call", "tool": name, "duration_ms": duration_ms},
        )
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
