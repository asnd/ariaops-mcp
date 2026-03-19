"""Shared constants and utilities for tool modules."""

from typing import Any

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
