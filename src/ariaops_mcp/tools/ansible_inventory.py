"""Opt-in tool for exporting an Ansible inventory from Aria Operations resources."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import mcp.types as types
import yaml

from ariaops_mcp.client import get_client
from ariaops_mcp.tools._common import PAGE_SIZE_MAX, format_error, write_guard

_RESOURCE_GROUPS: tuple[tuple[str, str], ...] = (
    ("clusters", "ClusterComputeResource"),
    ("nsx_edges", "NsxtEdgeNode"),
    ("nsx_managers", "NsxtManagerNode"),
)

_IP_IDENTIFIER_NAMES = {
    "ip",
    "ip_address",
    "ipaddress",
    "primaryipaddress",
    "primary_ip_address",
    "primary_ip",
    "address",
}


def tool_definitions() -> list[types.Tool]:
    return [
        types.Tool(
            name="export_ansible_inventory",
            description=(
                "Export an Ansible-compatible YAML inventory for vSphere clusters, "
                "NSX-T edge nodes, and NSX-T managers."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "outputPath": {
                        "type": "string",
                        "description": "Optional filesystem path to also write the generated inventory YAML.",
                    }
                },
                "required": [],
            },
        )
    ]


def _normalize_identifier_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _sanitize_hostname(value: str) -> str:
    hostname = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-")
    return hostname or "resource"


def _unique_hostname(base_hostname: str, identifier: str, seen_hostnames: set[str]) -> str:
    if base_hostname not in seen_hostnames:
        return base_hostname

    if identifier:
        candidate = f"{base_hostname}_{identifier[:8]}"
        if candidate not in seen_hostnames:
            return candidate

    for suffix in range(2, 10_001):
        candidate = f"{base_hostname}_{suffix}"
        if candidate not in seen_hostnames:
            return candidate

    raise ValueError(f"Unable to derive a unique hostname for resource '{base_hostname}'")


def _resource_name(resource: dict[str, Any]) -> str:
    resource_key = resource.get("resourceKey")
    if isinstance(resource_key, dict):
        name = resource_key.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    identifier = resource.get("identifier")
    return str(identifier).strip() if identifier else "resource"


def _resource_identity(resource: dict[str, Any]) -> dict[str, str]:
    identity: dict[str, str] = {}
    resource_key = resource.get("resourceKey")
    identifiers = resource_key.get("resourceIdentifiers") if isinstance(resource_key, dict) else None
    if isinstance(identifiers, list):
        for item in identifiers:
            if not isinstance(item, dict):
                continue
            identifier_type = item.get("identifierType")
            name = identifier_type.get("name") if isinstance(identifier_type, dict) else item.get("name")
            value = item.get("value")
            if isinstance(name, str) and isinstance(value, str) and name not in identity:
                identity[name] = value
    identifier = resource.get("identifier")
    if isinstance(identifier, str) and identifier:
        identity.setdefault("identifier", identifier)
    return identity


def _extract_ip_from_identity(identity: dict[str, str]) -> str | None:
    for name, value in identity.items():
        if _normalize_identifier_name(name) in _IP_IDENTIFIER_NAMES and value.strip():
            return value.strip()
    return None


def _property_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("property", "properties"):
        values = data.get(key)
        if isinstance(values, list):
            return [value for value in values if isinstance(value, dict)]
    return []


def _extract_ip_from_properties(data: dict[str, Any]) -> str | None:
    for item in _property_entries(data):
        name = item.get("name") or item.get("propertyKey")
        value = item.get("value")
        if isinstance(name, str) and isinstance(value, str):
            if _normalize_identifier_name(name) in _IP_IDENTIFIER_NAMES and value.strip():
                return value.strip()
    return None


def _resource_summary(resource: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "name": _resource_name(resource),
        "resourceStatusStates": resource.get("resourceStatusStates"),
        "resourceHealth": resource.get("resourceHealth"),
        "resourceStatus": resource.get("resourceStatus"),
        "monitoringInterval": resource.get("monitoringInterval"),
        "creationTime": resource.get("creationTime"),
    }
    return {key: value for key, value in summary.items() if value not in (None, [], {}, "")}


async def _query_resources(resource_kind: str) -> list[dict[str, Any]]:
    client = get_client()
    resources: list[dict[str, Any]] = []
    page = 0

    while True:
        data = await client.post(
            "/resources/query",
            {"resourceKind": [resource_kind]},
            idempotent=True,
            page=page,
            pageSize=PAGE_SIZE_MAX,
        )
        resource_list = data.get("resourceList", [])
        if isinstance(resource_list, list):
            resources.extend(resource for resource in resource_list if isinstance(resource, dict))
        page_info = data.get("pageInfo", {})
        total_count = (
            page_info.get("totalCount", len(resource_list))
            if isinstance(page_info, dict)
            else len(resource_list)
        )
        if not resource_list or (page + 1) * PAGE_SIZE_MAX >= total_count:
            break
        page += 1

    resources.sort(key=lambda resource: (_resource_name(resource).lower(), str(resource.get("identifier", ""))))
    return resources


async def _resource_ip(resource: dict[str, Any]) -> str | None:
    identity = _resource_identity(resource)
    ip_address = _extract_ip_from_identity(identity)
    if ip_address:
        return ip_address

    identifier = resource.get("identifier")
    if not isinstance(identifier, str) or not identifier:
        return None

    properties = await get_client().get(f"/resources/{quote(identifier, safe='')}/properties")
    return _extract_ip_from_properties(properties) if isinstance(properties, dict) else None


def _inventory_host_vars(resource: dict[str, Any], ip_address: str | None) -> dict[str, Any]:
    raw_resource_key = resource.get("resourceKey")
    resource_key: dict[str, Any] = raw_resource_key if isinstance(raw_resource_key, dict) else {}
    return {
        "ansible_host": ip_address,
        "ariaops_ip_address": ip_address,
        "ariaops_identifier": resource.get("identifier"),
        "ariaops_name": _resource_name(resource),
        "ariaops_adapter_kind": resource_key.get("adapterKindKey"),
        "ariaops_resource_kind": resource_key.get("resourceKindKey"),
        "ariaops_identity": _resource_identity(resource),
        "ariaops_summary": _resource_summary(resource),
    }


async def _build_inventory() -> dict[str, Any]:
    inventory: dict[str, Any] = {"all": {"children": {}}}
    seen_hostnames: set[str] = set()

    for group_name, resource_kind in _RESOURCE_GROUPS:
        group_hosts: dict[str, Any] = {}
        resources = await _query_resources(resource_kind)

        for resource in resources:
            base_hostname = _sanitize_hostname(_resource_name(resource))
            identifier = str(resource.get("identifier", ""))
            hostname = _unique_hostname(base_hostname, identifier, seen_hostnames)
            seen_hostnames.add(hostname)

            group_hosts[hostname] = _inventory_host_vars(resource, await _resource_ip(resource))

        inventory["all"]["children"][group_name] = {"hosts": group_hosts}

    return inventory


def tool_handlers() -> dict[str, Callable[[dict[str, Any]], Any]]:
    async def export_ansible_inventory(args: dict[str, Any]) -> str:
        disabled = write_guard()
        if disabled is not None:
            return disabled
        try:
            inventory = await _build_inventory()
            rendered = yaml.safe_dump(inventory, sort_keys=False, default_flow_style=False)
            output_path = args.get("outputPath")
            if output_path:
                path = Path(str(output_path))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(rendered, encoding="utf-8")
            return rendered
        except Exception as e:
            return format_error(e)

    return {"export_ansible_inventory": export_ansible_inventory}
