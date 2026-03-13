"""Capacity tools for Aria Operations (composite via stat keys)."""

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import mcp.types as types

from ariaops_mcp.client import get_client

logger = logging.getLogger(__name__)

# Capacity-related stat keys in Aria Operations
CAPACITY_STAT_KEYS = [
    "capacity|remainingCapacity",
    "capacity|timeRemaining",
    "capacity|badge|capacityRemaining",
    "cpu|capacity_contentionPct",
    "mem|host_usable",
    "diskspace|capacity",
    "diskspace|used",
]


def tool_definitions() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_capacity_remaining",
            description=(
                "Get remaining capacity stats for a resource (cluster, host, or datastore). "
                "Returns CPU, memory, and storage capacity metrics."
            ),
            inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "Resource UUID (cluster, host, or datastore)"},
                },
            },
        ),
        types.Tool(
            name="get_capacity_overview",
            description=(
                "Get a capacity overview across all clusters in a datacenter or all resources "
                "of a given kind. Returns total, used, and remaining capacity."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "adapterKind": {"type": "string", "default": "VMWARE"},
                    "resourceKind": {
                        "type": "string",
                        "default": "ClusterComputeResource",
                        "description": "e.g. ClusterComputeResource, Datastore, HostSystem",
                    },
                    "pageSize": {"type": "integer", "default": 20},
                },
            },
        ),
        types.Tool(
            name="list_policies",
            description="List capacity and alerting policies defined in Aria Operations.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


def tool_handlers() -> dict[str, Callable[[dict[str, Any]], Any]]:
    async def get_capacity_remaining(args: dict) -> str:
        client = get_client()
        results: dict[str, Any] = {"resourceId": args["id"], "capacityStats": {}}
        for stat_key in CAPACITY_STAT_KEYS:
            try:
                data = await client.get(
                    f"/resources/{args['id']}/stats/latest",
                    statKey=stat_key,
                )
                stat_list = data.get("values", [])
                if stat_list:
                    results["capacityStats"][stat_key] = stat_list[0].get("data", [])
            except httpx.HTTPError as exc:
                logger.warning(
                    "Failed to fetch capacity stat '%s' for resource '%s': %s",
                    stat_key,
                    args["id"],
                    exc,
                )
        return json.dumps(results, indent=2)

    async def get_capacity_overview(args: dict) -> str:
        client = get_client()
        adapter_kind = args.get("adapterKind", "VMWARE")
        resource_kind = args.get("resourceKind", "ClusterComputeResource")
        page_size = args.get("pageSize", 20)

        resources_data = await client.get(
            "/resources",
            adapterKind=adapter_kind,
            resourceKind=resource_kind,
            pageSize=page_size,
        )

        resource_ids = [r["identifier"] for r in resources_data.get("resourceList", [])]
        if not resource_ids:
            return json.dumps({"message": "No resources found", "resourceKind": resource_kind})

        body = {
            "resourceId": [{"resourceId": rid} for rid in resource_ids],
            "statKey": [{"key": k} for k in CAPACITY_STAT_KEYS],
        }
        stats_data = await client.post("/resources/stats/latest/query", body)

        return json.dumps(
            {
                "resourceKind": resource_kind,
                "resourceCount": len(resource_ids),
                "capacityStats": stats_data,
            },
            indent=2,
        )

    async def list_policies(args: dict) -> str:
        data = await get_client().get("/policies")
        return json.dumps(data, indent=2)

    return {
        "get_capacity_remaining": get_capacity_remaining,
        "get_capacity_overview": get_capacity_overview,
        "list_policies": list_policies,
    }
