"""Alert tools for Aria Operations."""

import json
from collections.abc import Callable
from typing import Any

import mcp.types as types

from ariaops_mcp.client import get_client

_PAGE_SIZE_DEFAULT = 50


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
                    "page": {"type": "integer", "default": 0},
                    "pageSize": {"type": "integer", "default": _PAGE_SIZE_DEFAULT},
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
                    "page": {"type": "integer", "default": 0},
                    "pageSize": {"type": "integer", "default": _PAGE_SIZE_DEFAULT},
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
                    "page": {"type": "integer", "default": 0},
                    "pageSize": {"type": "integer", "default": _PAGE_SIZE_DEFAULT},
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
        data = await get_client().get(
            "/alerts",
            status=args.get("status"),
            criticality=args.get("criticality"),
            resourceId=args.get("resourceId"),
            page=args.get("page", 0),
            pageSize=args.get("pageSize", _PAGE_SIZE_DEFAULT),
        )
        return json.dumps(data, indent=2)

    async def get_alert(args: dict) -> str:
        data = await get_client().get(f"/alerts/{args['id']}")
        return json.dumps(data, indent=2)

    async def query_alerts(args: dict) -> str:
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
            page=args.get("page", 0),
            pageSize=args.get("pageSize", _PAGE_SIZE_DEFAULT),
        )
        return json.dumps(data, indent=2)

    async def get_alert_notes(args: dict) -> str:
        data = await get_client().get(f"/alerts/{args['id']}/notes")
        return json.dumps(data, indent=2)

    async def list_alert_definitions(args: dict) -> str:
        data = await get_client().get(
            "/alertdefinitions",
            page=args.get("page", 0),
            pageSize=args.get("pageSize", _PAGE_SIZE_DEFAULT),
        )
        return json.dumps(data, indent=2)

    async def get_alert_definition(args: dict) -> str:
        data = await get_client().get(f"/alertdefinitions/{args['id']}")
        return json.dumps(data, indent=2)

    async def get_contributing_symptoms(args: dict) -> str:
        data = await get_client().get("/alerts/contributingsymptoms")
        return json.dumps(data, indent=2)

    return {
        "list_alerts": list_alerts,
        "get_alert": get_alert,
        "query_alerts": query_alerts,
        "get_alert_notes": get_alert_notes,
        "list_alert_definitions": list_alert_definitions,
        "get_alert_definition": get_alert_definition,
        "get_contributing_symptoms": get_contributing_symptoms,
    }
