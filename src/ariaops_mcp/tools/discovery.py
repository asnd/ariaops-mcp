"""Discovery / utility tools for Aria Operations."""

import json
from collections.abc import Callable
from typing import Any

import mcp.types as types

from ariaops_mcp.client import get_client


def tool_definitions() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_version",
            description="Get the current Aria Operations version and deployment info.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="list_collectors",
            description="List data collectors registered in Aria Operations.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="list_symptoms",
            description="List symptom definitions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "default": 0},
                    "pageSize": {"type": "integer", "default": 50},
                },
            },
        ),
        types.Tool(
            name="list_recommendations",
            description="List recommendations defined in Aria Operations.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="list_supermetrics",
            description="List super metrics defined in Aria Operations.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


def tool_handlers() -> dict[str, Callable[[dict[str, Any]], Any]]:
    async def get_version(args: dict) -> str:
        data = await get_client().get("/versions/current")
        return json.dumps(data, indent=2)

    async def list_collectors(args: dict) -> str:
        data = await get_client().get("/collectors")
        return json.dumps(data, indent=2)

    async def list_symptoms(args: dict) -> str:
        data = await get_client().get(
            "/symptomdefinitions",
            page=args.get("page", 0),
            pageSize=args.get("pageSize", 50),
        )
        return json.dumps(data, indent=2)

    async def list_recommendations(args: dict) -> str:
        data = await get_client().get("/recommendations")
        return json.dumps(data, indent=2)

    async def list_supermetrics(args: dict) -> str:
        data = await get_client().get("/supermetrics")
        return json.dumps(data, indent=2)

    return {
        "get_version": get_version,
        "list_collectors": list_collectors,
        "list_symptoms": list_symptoms,
        "list_recommendations": list_recommendations,
        "list_supermetrics": list_supermetrics,
    }
