"""Shared constants, helpers, and error handling for tool modules."""

import json
from typing import Any

import httpx

from ariaops_mcp.config import get_settings

PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 200
MAX_LIST_ITEMS = 50

# Fields returned per list_key when summaryOnly=true.
SUMMARY_FIELDS: dict[str, list[str]] = {
    "resourceList": ["identifier", "resourceKey"],
    "alerts": ["id", "alertDefinitionName", "status", "criticality", "resourceId"],
    "alertDefinitions": ["id", "name", "description", "adapterKindKey", "resourceKindKey"],
    "groups": ["id", "resourceKey"],
    "resourceGroups": ["id", "resourceKey"],
    "reportDefinitions": ["id", "name", "description"],
    "reports": ["id", "name", "status", "completedTime"],
    "symptomDefinitions": ["id", "name", "adapterKindKey", "resourceKindKey"],
    "recommendations": ["id", "description"],
}


def truncate_list_response(data: dict, list_key: str, *, page: int = 0, page_size: int = PAGE_SIZE_DEFAULT) -> dict:
    """Truncate list responses that exceed MAX_LIST_ITEMS and add pagination hints."""
    items = data.get(list_key, [])
    total_count: int | None = None
    page_info = data.get("pageInfo")
    if isinstance(page_info, dict):
        total_count = page_info.get("totalCount")

    if len(items) > MAX_LIST_ITEMS:
        data[list_key] = items[:MAX_LIST_ITEMS]
        data["_truncated"] = True
        data["_truncatedAt"] = MAX_LIST_ITEMS

    if total_count is not None:
        data["_totalCount"] = total_count
        returned = len(data.get(list_key, []))
        next_offset = (page * page_size) + returned
        if next_offset < total_count:
            data["_nextPage"] = page + 1
            data["_hint"] = (
                f"Showing {returned} of {total_count} items. "
                f"Use page={page + 1} (pageSize={page_size}) to fetch the next page."
            )

    return data


def filter_fields(items: list[dict[str, Any]], fields: list[str]) -> list[dict[str, Any]]:
    """Return only the requested top-level keys from each item."""
    if not fields:
        return items
    field_set = set(fields)
    return [{k: v for k, v in item.items() if k in field_set} for item in items if isinstance(item, dict)]


def summarize_items(items: list[dict[str, Any]], list_key: str) -> list[dict[str, Any]]:
    """Strip each item down to its summary fields for the given list_key."""
    keys = SUMMARY_FIELDS.get(list_key)
    if not keys:
        return items
    return filter_fields(items, keys)


def apply_response_shaping(
    data: dict, list_key: str, *, fields: list[str] | None = None, summary_only: bool = False
) -> dict:
    """Apply field filtering or summary mode to a list response.

    ``fields`` takes precedence over ``summary_only``.
    """
    items = data.get(list_key)
    if not isinstance(items, list):
        return data

    if fields:
        data[list_key] = filter_fields(items, fields)
    elif summary_only:
        data[list_key] = summarize_items(items, list_key)

    return data


def format_error(e: Exception) -> str:
    """Return a JSON error string for the given exception."""
    if isinstance(e, httpx.HTTPStatusError):
        return json.dumps(
            {"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]}
        )
    if isinstance(e, httpx.HTTPError):
        return json.dumps({"error": "Network error", "detail": str(e)})
    return json.dumps({"error": "Unexpected error", "detail": str(e)})


def writes_disabled_response() -> str:
    return json.dumps(
        {
            "error": "Write operations are disabled.",
            "detail": "Set ARIAOPS_ENABLE_WRITE_OPERATIONS=true to enable mutating tools.",
        }
    )


def write_guard() -> str | None:
    """Return an error string if writes are disabled, else None."""
    if not get_settings().enable_write_operations:
        return writes_disabled_response()
    return None
