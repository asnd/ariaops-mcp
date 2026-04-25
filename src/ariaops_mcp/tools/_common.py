"""Shared constants, helpers, and error handling for tool modules."""

import json

import httpx

from ariaops_mcp.config import get_settings

PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 200
MAX_LIST_ITEMS = 50


def truncate_list_response(data: dict, list_key: str) -> dict:
    items = data.get(list_key, [])
    if len(items) > MAX_LIST_ITEMS:
        data[list_key] = items[:MAX_LIST_ITEMS]
        data["_truncated"] = True
        data["_truncatedAt"] = MAX_LIST_ITEMS
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
