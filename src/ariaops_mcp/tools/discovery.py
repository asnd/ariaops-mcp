"""Discovery / utility tools for Aria Operations."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import mcp.types as types

from ariaops_mcp.client import get_client
from ariaops_mcp.tools._common import PAGE_SIZE_DEFAULT, PAGE_SIZE_MAX, truncate_list_response


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
                    "page": {"type": "integer", "default": 0, "minimum": 0},
                    "pageSize": {
                        "type": "integer",
                        "default": PAGE_SIZE_DEFAULT,
                        "minimum": 1,
                        "maximum": PAGE_SIZE_MAX,
                    },
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
        try:
            data = await get_client().get("/versions/current")
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def list_collectors(args: dict) -> str:
        try:
            data = await get_client().get("/collectors")
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def list_symptoms(args: dict) -> str:
        try:
            page = max(0, int(args.get("page", 0)))
            page_size = min(max(1, int(args.get("pageSize", PAGE_SIZE_DEFAULT))), PAGE_SIZE_MAX)
            data = await get_client().get(
                "/symptomdefinitions",
                page=page,
                pageSize=page_size,
            )
            data = truncate_list_response(data, "symptomDefinitions")
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def list_recommendations(args: dict) -> str:
        try:
            data = await get_client().get("/recommendations")
            data = truncate_list_response(data, "recommendations")
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def list_supermetrics(args: dict) -> str:
        try:
            data = await get_client().get("/supermetrics")
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    return {
        "get_version": get_version,
        "list_collectors": list_collectors,
        "list_symptoms": list_symptoms,
        "list_recommendations": list_recommendations,
        "list_supermetrics": list_supermetrics,
    }
