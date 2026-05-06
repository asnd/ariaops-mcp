"""Shared constants, helpers, and error handling for tool modules."""

import json
from typing import Any

import httpx

from ariaops_mcp.config import get_settings
from ariaops_mcp.logging_config import get_correlation_id

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
    base: dict[str, Any] = {}
    cid = get_correlation_id()
    if cid:
        base["correlation_id"] = cid

    if isinstance(e, httpx.HTTPStatusError):
        base.update({
            "error": str(e),
            "status_code": e.response.status_code,
            "detail": e.response.text[:500],
        })
    elif isinstance(e, httpx.HTTPError):
        base.update({"error": "Network error", "detail": str(e)})
    else:
        base.update({"error": "Unexpected error", "detail": str(e)})

    return json.dumps(base)


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
