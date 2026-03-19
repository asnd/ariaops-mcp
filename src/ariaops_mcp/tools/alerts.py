"""Alert tools for Aria Operations."""

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx
import mcp.types as types

from ariaops_mcp.client import get_client
from ariaops_mcp.tools._common import PAGE_SIZE_DEFAULT, PAGE_SIZE_MAX, apply_response_shaping, truncate_list_response

VALID_STATUS = {"ACTIVE", "CANCELLED", "SUSPENDED"}
VALID_CRITICALITY = {"CRITICAL", "IMMEDIATE", "WARNING", "INFORMATION"}


def tool_definitions() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_alerts",
            description="List active alerts. Filter by status, criticality, or resource.",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["ACTIVE", "CANCELLED", "SUSPENDED"],
                        "description": "Alert status",
                    },
                    "criticality": {
                        "type": "string",
                        "enum": ["CRITICAL", "IMMEDIATE", "WARNING", "INFORMATION"],
                        "description": "Alert criticality",
                    },
                    "resourceId": {"type": "string", "description": "Filter by resource UUID"},
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
            name="get_alert",
            description="Get details of a single alert by ID.",
            inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            },
        ),
        types.Tool(
            name="query_alerts",
            description="Advanced alert query with multiple filters.",
            inputSchema={
                "type": "object",
                "properties": {
                    "resourceIds": {"type": "array", "items": {"type": "string"}},
                    "alertCriticality": {"type": "array", "items": {"type": "string"}},
                    "alertStatus": {"type": "array", "items": {"type": "string"}},
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
            name="get_alert_notes",
            description="Get notes and comments on an alert.",
            inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            },
        ),
        types.Tool(
            name="list_alert_definitions",
            description="List alert definitions (templates).",
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
            name="get_alert_definition",
            description="Get details of an alert definition by ID.",
            inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            },
        ),
        types.Tool(
            name="get_contributing_symptoms",
            description="Get symptom definitions contributing to active alerts.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


def tool_handlers() -> dict[str, Callable[[dict[str, Any]], Any]]:
    async def list_alerts(args: dict) -> str:
        status = args.get("status")
        if status and status not in VALID_STATUS:
            return json.dumps({"error": f"Invalid status: {status}. Must be one of {sorted(VALID_STATUS)}"})
        criticality = args.get("criticality")
        if criticality and criticality not in VALID_CRITICALITY:
            return json.dumps(
                {"error": f"Invalid criticality: {criticality}. Must be one of {sorted(VALID_CRITICALITY)}"}
            )
        try:
            page = max(0, int(args.get("page", 0)))
            page_size = min(max(1, int(args.get("pageSize", PAGE_SIZE_DEFAULT))), PAGE_SIZE_MAX)
            data = await get_client().get(
                "/alerts",
                status=status,
                criticality=criticality,
                resourceId=args.get("resourceId"),
                page=page,
                pageSize=page_size,
            )
            data = truncate_list_response(data, "alerts", page=page, page_size=page_size)
            data = apply_response_shaping(data, "alerts", fields=args.get("fields"), summary_only=bool(args.get("summaryOnly", False)))
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def get_alert(args: dict) -> str:
        if not args.get("id"):
            return json.dumps({"error": "Missing required argument: id"})
        try:
            data = await get_client().get(f"/alerts/{quote(args['id'], safe='')}")
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def query_alerts(args: dict) -> str:
        try:
            page = max(0, int(args.get("page", 0)))
            page_size = min(max(1, int(args.get("pageSize", PAGE_SIZE_DEFAULT))), PAGE_SIZE_MAX)
            body: dict[str, Any] = {}
            if args.get("resourceIds"):
                body["resourceIds"] = args["resourceIds"]
            if args.get("alertCriticality"):
                body["alertCriticality"] = args["alertCriticality"]
            if args.get("alertStatus"):
                body["alertStatus"] = args["alertStatus"]
            data = await get_client().post(
                "/alerts/query",
                body,
                page=page,
                pageSize=page_size,
            )
            data = truncate_list_response(data, "alerts", page=page, page_size=page_size)
            data = apply_response_shaping(data, "alerts", fields=args.get("fields"), summary_only=bool(args.get("summaryOnly", False)))
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def get_alert_notes(args: dict) -> str:
        if not args.get("id"):
            return json.dumps({"error": "Missing required argument: id"})
        try:
            data = await get_client().get(f"/alerts/{quote(args['id'], safe='')}/notes")
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def list_alert_definitions(args: dict) -> str:
        try:
            page = max(0, int(args.get("page", 0)))
            page_size = min(max(1, int(args.get("pageSize", PAGE_SIZE_DEFAULT))), PAGE_SIZE_MAX)
            data = await get_client().get(
                "/alertdefinitions",
                page=page,
                pageSize=page_size,
            )
            data = truncate_list_response(data, "alertDefinitions", page=page, page_size=page_size)
            data = apply_response_shaping(data, "alertDefinitions", fields=args.get("fields"), summary_only=bool(args.get("summaryOnly", False)))
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def get_alert_definition(args: dict) -> str:
        if not args.get("id"):
            return json.dumps({"error": "Missing required argument: id"})
        try:
            data = await get_client().get(f"/alertdefinitions/{quote(args['id'], safe='')}")
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def get_contributing_symptoms(args: dict) -> str:
        try:
            data = await get_client().get("/alerts/contributingsymptoms")
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    return {
        "list_alerts": list_alerts,
        "get_alert": get_alert,
        "query_alerts": query_alerts,
        "get_alert_notes": get_alert_notes,
        "list_alert_definitions": list_alert_definitions,
        "get_alert_definition": get_alert_definition,
        "get_contributing_symptoms": get_contributing_symptoms,
    }
