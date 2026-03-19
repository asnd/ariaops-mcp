"""Report tools for Aria Operations."""

import base64
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx
import mcp.types as types

from ariaops_mcp.client import get_client
from ariaops_mcp.tools._common import PAGE_SIZE_DEFAULT, PAGE_SIZE_MAX, apply_response_shaping, truncate_list_response


def tool_definitions() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_report_definitions",
            description="List available report templates/definitions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "default": 0, "minimum": 0},
                    "pageSize": {
                        "type": "integer",
                        "default": PAGE_SIZE_DEFAULT,
                        "minimum": 1,
                        "maximum": PAGE_SIZE_MAX,
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Return only these top-level fields per item to reduce payload size.",
                    },
                    "summaryOnly": {
                        "type": "boolean",
                        "default": False,
                        "description": "When true, return only key identifying fields per item (compact mode).",
                    },
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
                    "page": {"type": "integer", "default": 0, "minimum": 0},
                    "pageSize": {
                        "type": "integer",
                        "default": PAGE_SIZE_DEFAULT,
                        "minimum": 1,
                        "maximum": PAGE_SIZE_MAX,
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Return only these top-level fields per item to reduce payload size.",
                    },
                    "summaryOnly": {
                        "type": "boolean",
                        "default": False,
                        "description": "When true, return only key identifying fields per item (compact mode).",
                    },
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
        try:
            page = max(0, int(args.get("page", 0)))
            page_size = min(max(1, int(args.get("pageSize", PAGE_SIZE_DEFAULT))), PAGE_SIZE_MAX)
            data = await get_client().get(
                "/reportdefinitions",
                page=page,
                pageSize=page_size,
            )
            data = truncate_list_response(data, "reportDefinitions", page=page, page_size=page_size)
            data = apply_response_shaping(
                data, "reportDefinitions",
                fields=args.get("fields"),
                summary_only=bool(args.get("summaryOnly", False)),
            )
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def get_report_definition(args: dict) -> str:
        if not args.get("id"):
            return json.dumps({"error": "Missing required argument: id"})
        try:
            data = await get_client().get(f"/reportdefinitions/{quote(args['id'], safe='')}")
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def list_reports(args: dict) -> str:
        try:
            page = max(0, int(args.get("page", 0)))
            page_size = min(max(1, int(args.get("pageSize", PAGE_SIZE_DEFAULT))), PAGE_SIZE_MAX)
            data = await get_client().get(
                "/reports",
                page=page,
                pageSize=page_size,
            )
            data = truncate_list_response(data, "reports", page=page, page_size=page_size)
            data = apply_response_shaping(
                data, "reports",
                fields=args.get("fields"),
                summary_only=bool(args.get("summaryOnly", False)),
            )
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def get_report(args: dict) -> str:
        if not args.get("id"):
            return json.dumps({"error": "Missing required argument: id"})
        try:
            data = await get_client().get(f"/reports/{quote(args['id'], safe='')}")
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def download_report(args: dict) -> str:
        if not args.get("id"):
            return json.dumps({"error": "Missing required argument: id"})
        try:
            raw = await get_client().get_bytes(f"/reports/{quote(args['id'], safe='')}/download")
            encoded = base64.b64encode(raw).decode("utf-8")
            return json.dumps({"reportId": args["id"], "encoding": "base64", "content": encoded})
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def list_report_schedules(args: dict) -> str:
        if not args.get("definitionId"):
            return json.dumps({"error": "Missing required argument: definitionId"})
        try:
            data = await get_client().get(f"/reportdefinitions/{quote(args['definitionId'], safe='')}/schedules")
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    return {
        "list_report_definitions": list_report_definitions,
        "get_report_definition": get_report_definition,
        "list_reports": list_reports,
        "get_report": get_report,
        "download_report": download_report,
        "list_report_schedules": list_report_schedules,
    }
