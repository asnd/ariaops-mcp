"""Metrics / stats tools for Aria Operations."""

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import mcp.types as types

from ariaops_mcp.client import get_client
from ariaops_mcp.tools._common import format_error

VALID_ROLL_UP_TYPES = {"AVG", "MIN", "MAX", "SUM", "NONE"}
VALID_INTERVAL_TYPES = {"MINUTES", "HOURS", "DAYS", "WEEKS", "MONTHS"}


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
        if not args.get("id"):
            return json.dumps({"error": "Missing required argument: id"})
        roll_up_type = args.get("rollUpType", "AVG")
        if roll_up_type and roll_up_type not in VALID_ROLL_UP_TYPES:
            return json.dumps(
                {"error": f"Invalid rollUpType: {roll_up_type}. Must be one of {sorted(VALID_ROLL_UP_TYPES)}"}
            )
        interval_type = args.get("intervalType", "HOURS")
        if interval_type and interval_type not in VALID_INTERVAL_TYPES:
            return json.dumps(
                {"error": f"Invalid intervalType: {interval_type}. Must be one of {sorted(VALID_INTERVAL_TYPES)}"}
            )
        try:
            data = await get_client().get(
                f"/resources/{quote(args['id'], safe='')}/stats",
                statKey=args.get("statKey"),
                begin=args.get("begin"),
                end=args.get("end"),
                rollUpType=roll_up_type,
                intervalType=interval_type,
                intervalQuantifier=args.get("intervalQuantifier", 1),
            )
            return json.dumps(data, indent=2)
        except Exception as e:
            return format_error(e)

    async def get_latest_stats(args: dict) -> str:
        if not args.get("id"):
            return json.dumps({"error": "Missing required argument: id"})
        try:
            data = await get_client().get(
                f"/resources/{quote(args['id'], safe='')}/stats/latest",
                statKey=args.get("statKey"),
            )
            return json.dumps(data, indent=2)
        except Exception as e:
            return format_error(e)

    async def query_stats(args: dict) -> str:
        if not args.get("resourceIds"):
            return json.dumps({"error": "Missing required argument: resourceIds"})
        if not args.get("statKeys"):
            return json.dumps({"error": "Missing required argument: statKeys"})
        roll_up_type = args.get("rollUpType")
        if roll_up_type and roll_up_type not in VALID_ROLL_UP_TYPES:
            return json.dumps(
                {"error": f"Invalid rollUpType: {roll_up_type}. Must be one of {sorted(VALID_ROLL_UP_TYPES)}"}
            )
        interval_type = args.get("intervalType")
        if interval_type and interval_type not in VALID_INTERVAL_TYPES:
            return json.dumps(
                {"error": f"Invalid intervalType: {interval_type}. Must be one of {sorted(VALID_INTERVAL_TYPES)}"}
            )
        try:
            body: dict[str, Any] = {
                "resourceId": [{"resourceId": rid} for rid in args["resourceIds"]],
                "statKey": [{"key": k} for k in args["statKeys"]],
            }
            if args.get("begin"):
                body["begin"] = args["begin"]
            if args.get("end"):
                body["end"] = args["end"]
            if roll_up_type:
                body["rollUpType"] = roll_up_type
            if interval_type:
                body["intervalType"] = interval_type
            data = await get_client().post("/resources/stats/query", body)
            return json.dumps(data, indent=2)
        except Exception as e:
            return format_error(e)

    async def query_latest_stats(args: dict) -> str:
        if not args.get("resourceIds"):
            return json.dumps({"error": "Missing required argument: resourceIds"})
        if not args.get("statKeys"):
            return json.dumps({"error": "Missing required argument: statKeys"})
        try:
            body = {
                "resourceId": [{"resourceId": rid} for rid in args["resourceIds"]],
                "statKey": [{"key": k} for k in args["statKeys"]],
            }
            data = await get_client().post("/resources/stats/latest/query", body)
            return json.dumps(data, indent=2)
        except Exception as e:
            return format_error(e)

    async def get_stat_keys(args: dict) -> str:
        if not args.get("id"):
            return json.dumps({"error": "Missing required argument: id"})
        try:
            data = await get_client().get(f"/resources/{quote(args['id'], safe='')}/statkeys")
            return json.dumps(data, indent=2)
        except Exception as e:
            return format_error(e)

    async def get_top_n_stats(args: dict) -> str:
        if not args.get("id"):
            return json.dumps({"error": "Missing required argument: id"})
        try:
            data = await get_client().get(
                f"/resources/{quote(args['id'], safe='')}/stats/topn",
                statKey=args.get("statKey"),
                topN=args.get("topN", 5),
            )
            return json.dumps(data, indent=2)
        except Exception as e:
            return format_error(e)

    async def list_properties_latest(args: dict) -> str:
        if not args.get("resourceIds"):
            return json.dumps({"error": "Missing required argument: resourceIds"})
        try:
            body: dict[str, Any] = {
                "resourceId": [{"resourceId": rid} for rid in args["resourceIds"]],
            }
            if args.get("propertyKeys"):
                body["propertyKey"] = [{"key": k} for k in args["propertyKeys"]]
            data = await get_client().post("/resources/properties/latest/query", body)
            return json.dumps(data, indent=2)
        except Exception as e:
            return format_error(e)

    return {
        "get_resource_stats": get_resource_stats,
        "get_latest_stats": get_latest_stats,
        "query_stats": query_stats,
        "query_latest_stats": query_latest_stats,
        "get_stat_keys": get_stat_keys,
        "get_top_n_stats": get_top_n_stats,
        "list_properties_latest": list_properties_latest,
    }
