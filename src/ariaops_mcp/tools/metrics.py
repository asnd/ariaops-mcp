"""Metrics / stats tools for Aria Operations."""

import json
from collections.abc import Callable
from typing import Any

import mcp.types as types

from ariaops_mcp.client import get_client


def tool_definitions() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_resource_stats",
            description="Get historical stats/metrics for a resource. Specify stat keys and time range.",
            inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "Resource UUID"},
                    "statKey": {"type": "string", "description": "Stat key, e.g. cpu|usage_average"},
                    "begin": {"type": "integer", "description": "Start time in epoch milliseconds"},
                    "end": {"type": "integer", "description": "End time in epoch milliseconds"},
                    "rollUpType": {"type": "string", "enum": ["AVG", "MIN", "MAX", "SUM", "NONE"], "default": "AVG"},
                    "intervalType": {
                        "type": "string",
                        "enum": ["MINUTES", "HOURS", "DAYS", "WEEKS", "MONTHS"],
                        "default": "HOURS",
                    },
                    "intervalQuantifier": {"type": "integer", "default": 1},
                },
            },
        ),
        types.Tool(
            name="get_latest_stats",
            description="Get the most recent stat values for a resource.",
            inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string"},
                    "statKey": {"type": "string", "description": "Optional: filter to specific stat key"},
                },
            },
        ),
        types.Tool(
            name="query_stats",
            description="Bulk stats query across multiple resources.",
            inputSchema={
                "type": "object",
                "required": ["resourceIds", "statKeys"],
                "properties": {
                    "resourceIds": {"type": "array", "items": {"type": "string"}},
                    "statKeys": {"type": "array", "items": {"type": "string"}},
                    "begin": {"type": "integer"},
                    "end": {"type": "integer"},
                    "rollUpType": {"type": "string", "default": "AVG"},
                    "intervalType": {"type": "string", "default": "HOURS"},
                },
            },
        ),
        types.Tool(
            name="query_latest_stats",
            description="Bulk latest stats query across multiple resources.",
            inputSchema={
                "type": "object",
                "required": ["resourceIds", "statKeys"],
                "properties": {
                    "resourceIds": {"type": "array", "items": {"type": "string"}},
                    "statKeys": {"type": "array", "items": {"type": "string"}},
                },
            },
        ),
        types.Tool(
            name="get_stat_keys",
            description="List available stat/metric keys for a resource.",
            inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            },
        ),
        types.Tool(
            name="get_top_n_stats",
            description="Get Top-N stat values for a resource.",
            inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string"},
                    "statKey": {"type": "string"},
                    "topN": {"type": "integer", "default": 5},
                },
            },
        ),
        types.Tool(
            name="list_properties_latest",
            description="Get latest property values for multiple resources.",
            inputSchema={
                "type": "object",
                "required": ["resourceIds"],
                "properties": {
                    "resourceIds": {"type": "array", "items": {"type": "string"}},
                    "propertyKeys": {"type": "array", "items": {"type": "string"}},
                },
            },
        ),
    ]


def tool_handlers() -> dict[str, Callable[[dict[str, Any]], Any]]:
    async def get_resource_stats(args: dict) -> str:
        data = await get_client().get(
            f"/resources/{args['id']}/stats",
            statKey=args.get("statKey"),
            begin=args.get("begin"),
            end=args.get("end"),
            rollUpType=args.get("rollUpType", "AVG"),
            intervalType=args.get("intervalType", "HOURS"),
            intervalQuantifier=args.get("intervalQuantifier", 1),
        )
        return json.dumps(data, indent=2)

    async def get_latest_stats(args: dict) -> str:
        data = await get_client().get(
            f"/resources/{args['id']}/stats/latest",
            statKey=args.get("statKey"),
        )
        return json.dumps(data, indent=2)

    async def query_stats(args: dict) -> str:
        body: dict[str, Any] = {
            "resourceId": [{"resourceId": rid} for rid in args["resourceIds"]],
            "statKey": [{"key": k} for k in args["statKeys"]],
        }
        if args.get("begin"):
            body["begin"] = args["begin"]
        if args.get("end"):
            body["end"] = args["end"]
        if args.get("rollUpType"):
            body["rollUpType"] = args["rollUpType"]
        if args.get("intervalType"):
            body["intervalType"] = args["intervalType"]
        data = await get_client().post("/resources/stats/query", body)
        return json.dumps(data, indent=2)

    async def query_latest_stats(args: dict) -> str:
        body = {
            "resourceId": [{"resourceId": rid} for rid in args["resourceIds"]],
            "statKey": [{"key": k} for k in args["statKeys"]],
        }
        data = await get_client().post("/resources/stats/latest/query", body)
        return json.dumps(data, indent=2)

    async def get_stat_keys(args: dict) -> str:
        data = await get_client().get(f"/resources/{args['id']}/statkeys")
        return json.dumps(data, indent=2)

    async def get_top_n_stats(args: dict) -> str:
        data = await get_client().get(
            f"/resources/{args['id']}/stats/topn",
            statKey=args.get("statKey"),
            topN=args.get("topN", 5),
        )
        return json.dumps(data, indent=2)

    async def list_properties_latest(args: dict) -> str:
        body: dict[str, Any] = {
            "resourceId": [{"resourceId": rid} for rid in args["resourceIds"]],
        }
        if args.get("propertyKeys"):
            body["propertyKey"] = [{"key": k} for k in args["propertyKeys"]]
        data = await get_client().post("/resources/properties/latest/query", body)
        return json.dumps(data, indent=2)

    return {
        "get_resource_stats": get_resource_stats,
        "get_latest_stats": get_latest_stats,
        "query_stats": query_stats,
        "query_latest_stats": query_latest_stats,
        "get_stat_keys": get_stat_keys,
        "get_top_n_stats": get_top_n_stats,
        "list_properties_latest": list_properties_latest,
    }
