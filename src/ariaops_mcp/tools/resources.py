"""Resource tools for Aria Operations."""

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx
import mcp.types as types

from ariaops_mcp.client import get_client
from ariaops_mcp.tools._common import PAGE_SIZE_DEFAULT, PAGE_SIZE_MAX, apply_response_shaping, truncate_list_response

VALID_RELATIONSHIP_TYPES = {"PARENT", "CHILD", "ALL"}


def tool_definitions() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_resources",
            description="List or search Aria Operations resources (VMs, hosts, clusters, datastores).",
            inputSchema={
                "type": "object",
                "properties": {
                    "resourceKind": {"type": "string", "description": "Filter by resource kind, e.g. VirtualMachine"},
                    "adapterKind": {"type": "string", "description": "Filter by adapter kind, e.g. VMWARE"},
                    "name": {"type": "string", "description": "Filter by resource name (partial match)"},
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
            name="get_resource",
            description="Get details of a single resource by its ID.",
            inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string", "description": "Resource UUID"}},
            },
        ),
        types.Tool(
            name="query_resources",
            description="Advanced resource query with multiple filters.",
            inputSchema={
                "type": "object",
                "properties": {
                    "adapterKind": {"type": "string"},
                    "resourceKind": {"type": "string"},
                    "name": {"type": "string"},
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
            name="get_resource_properties",
            description="Get configuration properties of a resource.",
            inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            },
        ),
        types.Tool(
            name="get_resource_relationships",
            description="Get parent/child relationships of a resource.",
            inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string"},
                    "relationshipType": {"type": "string", "enum": ["PARENT", "CHILD", "ALL"], "default": "ALL"},
                },
            },
        ),
        types.Tool(
            name="list_adapter_kinds",
            description="List all adapter kinds registered in Aria Operations.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="list_resource_kinds",
            description="List resource kinds for a given adapter kind.",
            inputSchema={
                "type": "object",
                "required": ["adapterKindKey"],
                "properties": {"adapterKindKey": {"type": "string", "description": "e.g. VMWARE"}},
            },
        ),
        types.Tool(
            name="list_resource_groups",
            description="List custom and dynamic resource groups.",
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
            name="get_resource_group_members",
            description="List members of a resource group.",
            inputSchema={
                "type": "object",
                "required": ["groupId"],
                "properties": {"groupId": {"type": "string"}},
            },
        ),
    ]


def tool_handlers() -> dict[str, Callable[[dict[str, Any]], Any]]:
    async def list_resources(args: dict) -> str:
        try:
            page = max(0, int(args.get("page", 0)))
            page_size = min(max(1, int(args.get("pageSize", PAGE_SIZE_DEFAULT))), PAGE_SIZE_MAX)
            data = await get_client().get(
                "/resources",
                resourceKind=args.get("resourceKind"),
                adapterKind=args.get("adapterKind"),
                name=args.get("name"),
                page=page,
                pageSize=page_size,
            )
            data = truncate_list_response(data, "resourceList", page=page, page_size=page_size)
            data = apply_response_shaping(
                data, "resourceList",
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

    async def get_resource(args: dict) -> str:
        if not args.get("id"):
            return json.dumps({"error": "Missing required argument: id"})
        try:
            data = await get_client().get(f"/resources/{quote(args['id'], safe='')}")
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def query_resources(args: dict) -> str:
        try:
            page = max(0, int(args.get("page", 0)))
            page_size = min(max(1, int(args.get("pageSize", PAGE_SIZE_DEFAULT))), PAGE_SIZE_MAX)
            body: dict[str, Any] = {}
            if args.get("adapterKind"):
                body["adapterKind"] = [args["adapterKind"]]
            if args.get("resourceKind"):
                body["resourceKind"] = [args["resourceKind"]]
            if args.get("name"):
                body["name"] = [args["name"]]
            data = await get_client().post(
                "/resources/query",
                body,
                page=page,
                pageSize=page_size,
            )
            data = truncate_list_response(data, "resourceList", page=page, page_size=page_size)
            data = apply_response_shaping(
                data, "resourceList",
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

    async def get_resource_properties(args: dict) -> str:
        if not args.get("id"):
            return json.dumps({"error": "Missing required argument: id"})
        try:
            data = await get_client().get(f"/resources/{quote(args['id'], safe='')}/properties")
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def get_resource_relationships(args: dict) -> str:
        if not args.get("id"):
            return json.dumps({"error": "Missing required argument: id"})
        rel = args.get("relationshipType", "ALL").upper()
        if rel not in VALID_RELATIONSHIP_TYPES:
            return json.dumps(
                {"error": f"Invalid relationshipType: {rel}. Must be one of {sorted(VALID_RELATIONSHIP_TYPES)}"}
            )
        try:
            rid = quote(args["id"], safe="")
            if rel == "ALL":
                data = await get_client().get(f"/resources/{rid}/relationships")
            else:
                data = await get_client().get(f"/resources/{rid}/relationships/{rel}")
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def list_adapter_kinds(args: dict) -> str:
        try:
            data = await get_client().get("/adapterkinds")
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def list_resource_kinds(args: dict) -> str:
        if not args.get("adapterKindKey"):
            return json.dumps({"error": "Missing required argument: adapterKindKey"})
        try:
            data = await get_client().get(f"/adapterkinds/{quote(args['adapterKindKey'], safe='')}/resourcekinds")
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def list_resource_groups(args: dict) -> str:
        try:
            page = max(0, int(args.get("page", 0)))
            page_size = min(max(1, int(args.get("pageSize", PAGE_SIZE_DEFAULT))), PAGE_SIZE_MAX)
            data = await get_client().get(
                "/resources/groups",
                page=page,
                pageSize=page_size,
            )
            data = truncate_list_response(data, "resourceGroups", page=page, page_size=page_size)
            data = apply_response_shaping(
                data, "resourceGroups",
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

    async def get_resource_group_members(args: dict) -> str:
        if not args.get("groupId"):
            return json.dumps({"error": "Missing required argument: groupId"})
        try:
            data = await get_client().get(f"/resources/groups/{quote(args['groupId'], safe='')}/members")
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    return {
        "list_resources": list_resources,
        "get_resource": get_resource,
        "query_resources": query_resources,
        "get_resource_properties": get_resource_properties,
        "get_resource_relationships": get_resource_relationships,
        "list_adapter_kinds": list_adapter_kinds,
        "list_resource_kinds": list_resource_kinds,
        "list_resource_groups": list_resource_groups,
        "get_resource_group_members": get_resource_group_members,
    }
