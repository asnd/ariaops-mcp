"""Report tools for Aria Operations."""

import base64
import json
from collections.abc import Callable
from typing import Any

import mcp.types as types

from ariaops_mcp.client import get_client

_PAGE_SIZE_DEFAULT = 50


def tool_definitions() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_report_definitions",
            description="List available report templates/definitions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "default": 0},
                    "pageSize": {"type": "integer", "default": _PAGE_SIZE_DEFAULT},
                },
            },
        ),
        types.Tool(
            name="get_report_definition",
            description="Get details of a report definition by ID.",
            inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            },
        ),
        types.Tool(
            name="list_reports",
            description="List generated reports.",
            inputSchema={
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "default": 0},
                    "pageSize": {"type": "integer", "default": _PAGE_SIZE_DEFAULT},
                },
            },
        ),
        types.Tool(
            name="get_report",
            description="Get metadata for a generated report.",
            inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            },
        ),
        types.Tool(
            name="download_report",
            description="Download a generated report. Returns base64-encoded content and MIME type.",
            inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            },
        ),
        types.Tool(
            name="list_report_schedules",
            description="List schedules for a report definition.",
            inputSchema={
                "type": "object",
                "required": ["definitionId"],
                "properties": {"definitionId": {"type": "string"}},
            },
        ),
    ]


def tool_handlers() -> dict[str, Callable[[dict[str, Any]], Any]]:
    async def list_report_definitions(args: dict) -> str:
        data = await get_client().get(
            "/reportdefinitions",
            page=args.get("page", 0),
            pageSize=args.get("pageSize", _PAGE_SIZE_DEFAULT),
        )
        return json.dumps(data, indent=2)

    async def get_report_definition(args: dict) -> str:
        data = await get_client().get(f"/reportdefinitions/{args['id']}")
        return json.dumps(data, indent=2)

    async def list_reports(args: dict) -> str:
        data = await get_client().get(
            "/reports",
            page=args.get("page", 0),
            pageSize=args.get("pageSize", _PAGE_SIZE_DEFAULT),
        )
        return json.dumps(data, indent=2)

    async def get_report(args: dict) -> str:
        data = await get_client().get(f"/reports/{args['id']}")
        return json.dumps(data, indent=2)

    async def download_report(args: dict) -> str:
        raw = await get_client().get_bytes(f"/reports/{args['id']}/download")
        encoded = base64.b64encode(raw).decode("utf-8")
        return json.dumps({"reportId": args["id"], "encoding": "base64", "content": encoded})

    async def list_report_schedules(args: dict) -> str:
        data = await get_client().get(f"/reportdefinitions/{args['definitionId']}/schedules")
        return json.dumps(data, indent=2)

    return {
        "list_report_definitions": list_report_definitions,
        "get_report_definition": get_report_definition,
        "list_reports": list_reports,
        "get_report": get_report,
        "download_report": download_report,
        "list_report_schedules": list_report_schedules,
    }
