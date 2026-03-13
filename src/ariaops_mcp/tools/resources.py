"""Resource tools for Aria Operations."""

import json
from collections.abc import Callable
from typing import Any

import mcp.types as types

from ariaops_mcp.client import get_client

_PAGE_SIZE_DEFAULT = 50


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
                    "page": {"type": "integer", "default": 0},
                    "pageSize": {"type": "integer", "default": _PAGE_SIZE_DEFAULT},
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
                    "page": {"type": "integer", "default": 0},
                    "pageSize": {"type": "integer", "default": _PAGE_SIZE_DEFAULT},
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
                    "page": {"type": "integer", "default": 0},
                    "pageSize": {"type": "integer", "default": _PAGE_SIZE_DEFAULT},
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
        client = get_client()
        data = await client.get(
            "/resources",
            resourceKind=args.get("resourceKind"),
            adapterKind=args.get("adapterKind"),
            name=args.get("name"),
            page=args.get("page", 0),
            pageSize=args.get("pageSize", _PAGE_SIZE_DEFAULT),
        )
        return json.dumps(data, indent=2)

    async def get_resource(args: dict) -> str:
        data = await get_client().get(f"/resources/{args['id']}")
        return json.dumps(data, indent=2)

    async def query_resources(args: dict) -> str:
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
            page=args.get("page", 0),
            pageSize=args.get("pageSize", _PAGE_SIZE_DEFAULT),
        )
        return json.dumps(data, indent=2)

    async def get_resource_properties(args: dict) -> str:
        data = await get_client().get(f"/resources/{args['id']}/properties")
        return json.dumps(data, indent=2)

    async def get_resource_relationships(args: dict) -> str:
        rel = args.get("relationshipType", "ALL").upper()
        if rel == "ALL":
            data = await get_client().get(f"/resources/{args['id']}/relationships")
        else:
            data = await get_client().get(f"/resources/{args['id']}/relationships/{rel}")
        return json.dumps(data, indent=2)

    async def list_adapter_kinds(args: dict) -> str:
        data = await get_client().get("/adapterkinds")
        return json.dumps(data, indent=2)

    async def list_resource_kinds(args: dict) -> str:
        data = await get_client().get(f"/adapterkinds/{args['adapterKindKey']}/resourcekinds")
        return json.dumps(data, indent=2)

    async def list_resource_groups(args: dict) -> str:
        data = await get_client().get(
            "/resources/groups",
            page=args.get("page", 0),
            pageSize=args.get("pageSize", _PAGE_SIZE_DEFAULT),
        )
        return json.dumps(data, indent=2)

    async def get_resource_group_members(args: dict) -> str:
        data = await get_client().get(f"/resources/groups/{args['groupId']}/members")
        return json.dumps(data, indent=2)

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
