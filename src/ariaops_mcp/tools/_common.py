"""Shared constants, helpers, and error handling for tool modules."""

import json
from typing import Any

import httpx

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


def writes_disabled_response(instance_name: str | None = None) -> str:
    detail = "Set ARIAOPS_ENABLE_WRITE_OPERATIONS=true to enable mutating tools."
    if instance_name:
        detail = (
            f"Write operations are disabled on instance '{instance_name}'. "
            "Set write_enabled: true in instances.yaml for this instance."
        )
    return json.dumps({"error": "Write operations are disabled.", "detail": detail})


def write_guard(instance_name: str | None = None) -> str | None:
    """Return an error string if writes are disabled, else None.

    Checks both the instance-level write_enabled flag (if multi-instance)
    and the legacy global ARIAOPS_ENABLE_WRITE_OPERATIONS.
    """
    if instance_name:
        from ariaops_mcp.resolver import check_write_access
        error = check_write_access(instance_name)
        if error:
            return json.dumps({"error": "Write operations are disabled.", "detail": error})
        return None

    # Legacy fallback: global write toggle
    from ariaops_mcp.config import get_settings
    if not get_settings().enable_write_operations:
        return writes_disabled_response()
    return None


async def resolve_client(args: dict[str, Any]) -> "AriaOpsClient":  # type: ignore[name-defined]  # noqa: F821
    """Resolve the AriaOpsClient for a tool call.

    Extracts 'instance' from tool args (optional), resolves via InstanceResolver,
    and returns the appropriate client from the pool.

    Falls back to legacy get_client() if no multi-instance config is loaded.
    """
    from ariaops_mcp.client import get_client, get_client_pool
    from ariaops_mcp.instances import get_instance_registry
    from ariaops_mcp.resolver import resolve_instance_name

    instance_arg = args.pop("instance", None)

    registry = get_instance_registry()
    if not registry.is_loaded or not registry.config.instances:
        # No multi-instance config — use legacy singleton
        return get_client()

    try:
        instance_name = resolve_instance_name(explicit=instance_arg)
    except Exception:
        # If resolution fails and we have a legacy client, fall back
        if instance_arg is None:
            return get_client()
        raise

    pool = get_client_pool()
    return await pool.get_client(instance_name)


def get_instance_from_args(args: dict[str, Any]) -> str | None:
    """Extract and return the instance name from args without removing it."""
    return args.get("instance")
