"""Write-operation tools for Aria Operations (feature-flag gated)."""

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx
import mcp.types as types

from ariaops_mcp.client import get_client
from ariaops_mcp.config import get_settings

MAX_NOTE_LENGTH = 2000
VALID_ALERT_ACTIONS = {"CANCEL", "SUSPEND"}


def _write_ops_enabled() -> bool:
    try:
        return bool(get_settings().enable_write_operations)
    except Exception:
        return False


def _disabled_response() -> str:
    return json.dumps(
        {
            "error": "Write operations are disabled by configuration.",
            "next_step": "Set ARIAOPS_ENABLE_WRITE_OPERATIONS=true and restart the server to enable write tools.",
        }
    )


def _contains_disallowed_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 and ch not in {"\n", "\r", "\t"} for ch in value)


def tool_definitions() -> list[types.Tool]:
    return [
        types.Tool(
            name="add_alert_note",
            description="Add a note to an alert. Requires write operations to be enabled.",
            inputSchema={
                "type": "object",
                "required": ["id", "note"],
                "properties": {
                    "id": {"type": "string", "description": "Alert ID"},
                    "note": {
                        "type": "string",
                        "description": "Note text to attach to the alert.",
                        "minLength": 1,
                        "maxLength": MAX_NOTE_LENGTH,
                    },
                },
            },
        ),
        types.Tool(
            name="set_alert_status",
            description="Change alert status by action (CANCEL or SUSPEND). Requires write operations to be enabled.",
            inputSchema={
                "type": "object",
                "required": ["id", "action"],
                "properties": {
                    "id": {"type": "string", "description": "Alert ID"},
                    "action": {
                        "type": "string",
                        "enum": sorted(VALID_ALERT_ACTIONS),
                        "description": "Status transition action",
                    },
                },
            },
        ),
    ]


def tool_handlers() -> dict[str, Callable[[dict[str, Any]], Any]]:
    async def add_alert_note(args: dict) -> str:
        if not _write_ops_enabled():
            return _disabled_response()

        alert_id = str(args.get("id", "")).strip()
        note = str(args.get("note", "")).strip()
        if not alert_id:
            return json.dumps({"error": "Missing required argument: id"})
        if not note:
            return json.dumps({"error": "Missing required argument: note"})
        if len(note) > MAX_NOTE_LENGTH:
            return json.dumps({"error": f"note exceeds maximum length of {MAX_NOTE_LENGTH} characters"})
        if _contains_disallowed_control_chars(note):
            return json.dumps({"error": "note contains disallowed control characters"})

        try:
            data = await get_client().post(
                f"/alerts/{quote(alert_id, safe='')}/notes",
                {"note": note},
            )
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    async def set_alert_status(args: dict) -> str:
        if not _write_ops_enabled():
            return _disabled_response()

        alert_id = str(args.get("id", "")).strip()
        action = str(args.get("action", "")).strip().upper()
        if not alert_id:
            return json.dumps({"error": "Missing required argument: id"})
        if not action:
            return json.dumps({"error": "Missing required argument: action"})
        if action not in VALID_ALERT_ACTIONS:
            return json.dumps({"error": f"Invalid action: {action}. Must be one of {sorted(VALID_ALERT_ACTIONS)}"})

        try:
            data = await get_client().post(f"/alerts/{quote(alert_id, safe='')}/{action.lower()}", {})
            return json.dumps(data, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": str(e), "status_code": e.response.status_code, "detail": e.response.text[:500]})
        except httpx.HTTPError as e:
            return json.dumps({"error": "Network error", "detail": str(e)})
        except Exception as e:
            return json.dumps({"error": "Unexpected error", "detail": str(e)})

    return {
        "add_alert_note": add_alert_note,
        "set_alert_status": set_alert_status,
    }
