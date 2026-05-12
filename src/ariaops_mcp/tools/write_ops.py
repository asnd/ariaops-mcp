"""Write (mutating) tools for Aria Operations.

All tools in this module are gated behind ARIAOPS_ENABLE_WRITE_OPERATIONS=true.
When the flag is false (default) the tools are never registered on the server,
so they never appear in the MCP tool list at all.  The runtime guard inside each
handler is a defence-in-depth safeguard.
"""

import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import mcp.types as types

from ariaops_mcp.client import get_client
from ariaops_mcp.tools._common import format_error, write_guard

# ── constants ────────────────────────────────────────────────────────────────

VALID_ALERT_ACTIONS = {"CANCEL", "SUSPEND", "ACKNOWLEDGE"}
NOTE_MAX_LEN = 4000
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# ── helpers ───────────────────────────────────────────────────────────────────


def _validate_note(note: str) -> str | None:
    """Return an error message if the note is invalid, else None."""
    if not note or not note.strip():
        return "Note text must not be empty."
    if len(note) > NOTE_MAX_LEN:
        return f"Note text exceeds maximum length of {NOTE_MAX_LEN} characters."
    if _CONTROL_CHAR_RE.search(note):
        return "Note text contains disallowed control characters."
    return None


# ── tool_definitions ──────────────────────────────────────────────────────────


def tool_definitions() -> list[types.Tool]:
    return [
        # ── Alerts ────────────────────────────────────────────────────────────
        types.Tool(
            name="modify_alerts",
            description=(
                "Bulk-modify alerts: cancel, suspend, or acknowledge one or more alerts by ID. "
                "Requires ARIAOPS_ENABLE_WRITE_OPERATIONS=true."
            ),
            inputSchema={
                "type": "object",
                "required": ["alertIds", "action"],
                "properties": {
                    "alertIds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "List of alert UUIDs to modify.",
                    },
                    "action": {
                        "type": "string",
                        "enum": sorted(VALID_ALERT_ACTIONS),
                        "description": "Action to apply: CANCEL, SUSPEND, or ACKNOWLEDGE.",
                    },
                },
            },
        ),
        types.Tool(
            name="add_alert_note",
            description=(
                "Add a note/comment to an alert. "
                "Requires ARIAOPS_ENABLE_WRITE_OPERATIONS=true."
            ),
            inputSchema={
                "type": "object",
                "required": ["id", "note"],
                "properties": {
                    "id": {"type": "string", "description": "Alert UUID."},
                    "note": {
                        "type": "string",
                        "maxLength": NOTE_MAX_LEN,
                        "description": "Note text to add.",
                    },
                },
            },
        ),
        types.Tool(
            name="delete_alert_note",
            description=(
                "Delete a specific note from an alert. "
                "Requires ARIAOPS_ENABLE_WRITE_OPERATIONS=true."
            ),
            inputSchema={
                "type": "object",
                "required": ["id", "noteId"],
                "properties": {
                    "id": {"type": "string", "description": "Alert UUID."},
                    "noteId": {"type": "string", "description": "Note UUID to delete."},
                },
            },
        ),
        types.Tool(
            name="delete_canceled_alerts",
            description=(
                "Delete canceled alerts matching the given criteria. "
                "At least one filter (alertIds, resourceIds, or olderThanDays) is recommended. "
                "Requires ARIAOPS_ENABLE_WRITE_OPERATIONS=true."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "alertIds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Specific alert UUIDs to delete (must be in CANCELLED state).",
                    },
                    "resourceIds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Delete canceled alerts for these resource UUIDs.",
                    },
                    "olderThanDays": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Delete canceled alerts older than this many days.",
                    },
                },
            },
        ),
        # ── Resource maintenance ───────────────────────────────────────────────
        types.Tool(
            name="mark_resources_maintained",
            description=(
                "Put one or more resources into maintenance mode (suppresses alerts). "
                "Requires ARIAOPS_ENABLE_WRITE_OPERATIONS=true."
            ),
            inputSchema={
                "type": "object",
                "required": ["resourceIds"],
                "properties": {
                    "resourceIds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "Resource UUIDs to mark as maintained.",
                    }
                },
            },
        ),
        types.Tool(
            name="unmark_resources_maintained",
            description=(
                "Take one or more resources out of maintenance mode. "
                "Requires ARIAOPS_ENABLE_WRITE_OPERATIONS=true."
            ),
            inputSchema={
                "type": "object",
                "required": ["resourceIds"],
                "properties": {
                    "resourceIds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "Resource UUIDs to unmark from maintenance.",
                    }
                },
            },
        ),
        # ── Maintenance schedules ─────────────────────────────────────────────
        types.Tool(
            name="create_maintenance_schedule",
            description=(
                "Create a maintenance schedule for one or more resources. "
                "Requires ARIAOPS_ENABLE_WRITE_OPERATIONS=true."
            ),
            inputSchema={
                "type": "object",
                "required": ["name", "resourceIds", "startTime", "endTime"],
                "properties": {
                    "name": {"type": "string", "description": "Schedule name."},
                    "resourceIds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "Resource UUIDs to schedule maintenance for.",
                    },
                    "startTime": {
                        "type": "integer",
                        "description": "Start time in milliseconds since epoch.",
                    },
                    "endTime": {
                        "type": "integer",
                        "description": "End time in milliseconds since epoch.",
                    },
                    "recurrence": {
                        "type": "string",
                        "description": "Optional recurrence rule (iCal RRULE format).",
                    },
                },
            },
        ),
        types.Tool(
            name="update_maintenance_schedule",
            description=(
                "Update an existing maintenance schedule. "
                "Requires ARIAOPS_ENABLE_WRITE_OPERATIONS=true."
            ),
            inputSchema={
                "type": "object",
                "required": ["id", "name", "resourceIds", "startTime", "endTime"],
                "properties": {
                    "id": {"type": "string", "description": "Maintenance schedule UUID."},
                    "name": {"type": "string", "description": "Schedule name."},
                    "resourceIds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "Resource UUIDs included in the schedule.",
                    },
                    "startTime": {
                        "type": "integer",
                        "description": "Start time in milliseconds since epoch.",
                    },
                    "endTime": {
                        "type": "integer",
                        "description": "End time in milliseconds since epoch.",
                    },
                    "recurrence": {
                        "type": "string",
                        "description": "Optional recurrence rule (iCal RRULE format).",
                    },
                },
            },
        ),
        types.Tool(
            name="delete_maintenance_schedule",
            description=(
                "Delete one or more maintenance schedules by ID. "
                "Requires ARIAOPS_ENABLE_WRITE_OPERATIONS=true."
            ),
            inputSchema={
                "type": "object",
                "required": ["ids"],
                "properties": {
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "Maintenance schedule UUIDs to delete.",
                    }
                },
            },
        ),
        # ── Reports ───────────────────────────────────────────────────────────
        types.Tool(
            name="generate_report",
            description=(
                "Generate (create) a report from a report definition for a given resource. "
                "Requires ARIAOPS_ENABLE_WRITE_OPERATIONS=true."
            ),
            inputSchema={
                "type": "object",
                "required": ["reportDefinitionId", "resourceId"],
                "properties": {
                    "reportDefinitionId": {
                        "type": "string",
                        "description": "UUID of the report definition to use.",
                    },
                    "resourceId": {
                        "type": "string",
                        "description": "UUID of the resource to generate the report for.",
                    },
                },
            },
        ),
        types.Tool(
            name="delete_report",
            description=(
                "Delete a generated report by ID. "
                "Requires ARIAOPS_ENABLE_WRITE_OPERATIONS=true."
            ),
            inputSchema={
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "description": "Report UUID to delete."}
                },
            },
        ),
        types.Tool(
            name="create_report_schedule",
            description=(
                "Create a schedule to automatically generate a report for a report definition. "
                "Requires ARIAOPS_ENABLE_WRITE_OPERATIONS=true."
            ),
            inputSchema={
                "type": "object",
                "required": ["reportDefinitionId", "resourceIds", "recurrence"],
                "properties": {
                    "reportDefinitionId": {
                        "type": "string",
                        "description": "UUID of the report definition.",
                    },
                    "resourceIds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "Resource UUIDs the schedule applies to.",
                    },
                    "recurrence": {
                        "type": "string",
                        "description": "Recurrence rule (iCal RRULE format, e.g. FREQ=WEEKLY;BYDAY=MO).",
                    },
                    "emailConfig": {
                        "type": "object",
                        "description": "Optional email delivery configuration.",
                    },
                },
            },
        ),
        types.Tool(
            name="update_report_schedule",
            description=(
                "Update an existing report schedule. "
                "Requires ARIAOPS_ENABLE_WRITE_OPERATIONS=true."
            ),
            inputSchema={
                "type": "object",
                "required": ["reportDefinitionId", "scheduleId", "resourceIds", "recurrence"],
                "properties": {
                    "reportDefinitionId": {
                        "type": "string",
                        "description": "UUID of the report definition.",
                    },
                    "scheduleId": {
                        "type": "string",
                        "description": "UUID of the schedule to update.",
                    },
                    "resourceIds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "Resource UUIDs for the schedule.",
                    },
                    "recurrence": {
                        "type": "string",
                        "description": "Recurrence rule (iCal RRULE format).",
                    },
                    "emailConfig": {
                        "type": "object",
                        "description": "Optional email delivery configuration.",
                    },
                },
            },
        ),
        types.Tool(
            name="delete_report_schedule",
            description=(
                "Delete a report schedule for a report definition. "
                "Requires ARIAOPS_ENABLE_WRITE_OPERATIONS=true."
            ),
            inputSchema={
                "type": "object",
                "required": ["reportDefinitionId", "scheduleId"],
                "properties": {
                    "reportDefinitionId": {
                        "type": "string",
                        "description": "UUID of the report definition.",
                    },
                    "scheduleId": {
                        "type": "string",
                        "description": "UUID of the schedule to delete.",
                    },
                },
            },
        ),
        # ── Resources ─────────────────────────────────────────────────────────
        types.Tool(
            name="create_resource",
            description=(
                "Create a new resource associated with a given adapter kind. "
                "The resource object must follow the Aria Operations resource schema. "
                "Requires ARIAOPS_ENABLE_WRITE_OPERATIONS=true."
            ),
            inputSchema={
                "type": "object",
                "required": ["adapterKindKey", "resourceKindKey", "resourceIdentifiers"],
                "properties": {
                    "adapterKindKey": {
                        "type": "string",
                        "description": "Adapter kind key (e.g. 'VMWARE').",
                    },
                    "resourceKindKey": {
                        "type": "string",
                        "description": "Resource kind key (e.g. 'VirtualMachine').",
                    },
                    "resourceIdentifiers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["identifierType", "value"],
                            "properties": {
                                "identifierType": {
                                    "type": "object",
                                    "required": ["name"],
                                    "properties": {"name": {"type": "string"}},
                                },
                                "value": {"type": "string"},
                            },
                        },
                        "minItems": 1,
                        "description": "Identifier name/value pairs that uniquely identify the resource.",
                    },
                    "resourceName": {
                        "type": "string",
                        "description": "Optional display name for the resource.",
                    },
                    "adapterInstanceId": {
                        "type": "string",
                        "description": (
                            "Optional adapter instance UUID to associate with. "
                            "If omitted the resource is created under the adapter kind directly."
                        ),
                    },
                },
            },
        ),
        types.Tool(
            name="update_resource",
            description=(
                "Update an existing resource's metadata. "
                "Provide the full resource object (use get_resource to obtain the current state). "
                "Requires ARIAOPS_ENABLE_WRITE_OPERATIONS=true."
            ),
            inputSchema={
                "type": "object",
                "required": ["resource"],
                "properties": {
                    "resource": {
                        "type": "object",
                        "description": "Full resource object as returned by get_resource, with modifications applied.",
                    }
                },
            },
        ),
        types.Tool(
            name="delete_resources",
            description=(
                "Delete one or more resources by ID. This is irreversible. "
                "Requires ARIAOPS_ENABLE_WRITE_OPERATIONS=true."
            ),
            inputSchema={
                "type": "object",
                "required": ["resourceIds"],
                "properties": {
                    "resourceIds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "Resource UUIDs to delete.",
                    }
                },
            },
        ),
    ]


# ── tool_handlers ─────────────────────────────────────────────────────────────


def tool_handlers() -> dict[str, Callable[[dict[str, Any]], Any]]:  # noqa: C901
    # ── Alerts ────────────────────────────────────────────────────────────────

    async def modify_alerts(args: dict) -> str:
        if (g := write_guard()):
            return g
        alert_ids = args.get("alertIds")
        if not alert_ids:
            return json.dumps({"error": "Missing required argument: alertIds"})
        action = (args.get("action") or "").upper()
        if action not in VALID_ALERT_ACTIONS:
            return json.dumps(
                {"error": f"Invalid action '{action}'. Must be one of {sorted(VALID_ALERT_ACTIONS)}."}
            )
        try:
            body: dict[str, Any] = {"alertIds": alert_ids, "alertAction": action}
            data = await get_client().post("/alerts", body)
            return json.dumps(data, indent=2)
        except Exception as e:
            return format_error(e)

    async def add_alert_note(args: dict) -> str:
        if (g := write_guard()):
            return g
        alert_id = args.get("id")
        if not alert_id:
            return json.dumps({"error": "Missing required argument: id"})
        note = args.get("note", "")
        if (msg := _validate_note(note)):
            return json.dumps({"error": msg})
        try:
            data = await get_client().post(
                f"/alerts/{quote(alert_id, safe='')}/notes",
                {"note": note},
            )
            return json.dumps(data, indent=2)
        except Exception as e:
            return format_error(e)

    async def delete_alert_note(args: dict) -> str:
        if (g := write_guard()):
            return g
        alert_id = args.get("id")
        note_id = args.get("noteId")
        if not alert_id:
            return json.dumps({"error": "Missing required argument: id"})
        if not note_id:
            return json.dumps({"error": "Missing required argument: noteId"})
        try:
            data = await get_client().delete(
                f"/alerts/{quote(alert_id, safe='')}/notes/{quote(note_id, safe='')}"
            )
            return json.dumps(data if data else {"status": "deleted"}, indent=2)
        except Exception as e:
            return format_error(e)

    async def delete_canceled_alerts(args: dict) -> str:
        if (g := write_guard()):
            return g
        body: dict[str, Any] = {}
        if args.get("alertIds"):
            body["alertIds"] = args["alertIds"]
        if args.get("resourceIds"):
            body["resourceIds"] = args["resourceIds"]
        if args.get("olderThanDays") is not None:
            body["olderThanDays"] = int(args["olderThanDays"])
        try:
            data = await get_client().post("/alerts/bulk/delete", body)
            return json.dumps(data if data else {"status": "deleted"}, indent=2)
        except Exception as e:
            return format_error(e)

    # ── Resource maintenance ───────────────────────────────────────────────────

    async def mark_resources_maintained(args: dict) -> str:
        if (g := write_guard()):
            return g
        resource_ids = args.get("resourceIds")
        if not resource_ids:
            return json.dumps({"error": "Missing required argument: resourceIds"})
        try:
            data = await get_client().put("/resources/maintained", {"resourceIds": resource_ids})
            return json.dumps(data if data else {"status": "ok"}, indent=2)
        except Exception as e:
            return format_error(e)

    async def unmark_resources_maintained(args: dict) -> str:
        if (g := write_guard()):
            return g
        resource_ids = args.get("resourceIds")
        if not resource_ids:
            return json.dumps({"error": "Missing required argument: resourceIds"})
        try:
            data = await get_client().delete("/resources/maintained", {"resourceIds": resource_ids})
            return json.dumps(data if data else {"status": "ok"}, indent=2)
        except Exception as e:
            return format_error(e)

    # ── Maintenance schedules ─────────────────────────────────────────────────

    async def create_maintenance_schedule(args: dict) -> str:
        if (g := write_guard()):
            return g
        for field in ("name", "resourceIds", "startTime", "endTime"):
            if not args.get(field) and args.get(field) != 0:
                return json.dumps({"error": f"Missing required argument: {field}"})
        body: dict[str, Any] = {
            "name": args["name"],
            "resourceIds": args["resourceIds"],
            "startTime": int(args["startTime"]),
            "endTime": int(args["endTime"]),
        }
        if args.get("recurrence"):
            body["recurrence"] = args["recurrence"]
        try:
            data = await get_client().post("/maintenanceschedules", body)
            return json.dumps(data, indent=2)
        except Exception as e:
            return format_error(e)

    async def update_maintenance_schedule(args: dict) -> str:
        if (g := write_guard()):
            return g
        for field in ("id", "name", "resourceIds", "startTime", "endTime"):
            if not args.get(field) and args.get(field) != 0:
                return json.dumps({"error": f"Missing required argument: {field}"})
        body: dict[str, Any] = {
            "id": args["id"],
            "name": args["name"],
            "resourceIds": args["resourceIds"],
            "startTime": int(args["startTime"]),
            "endTime": int(args["endTime"]),
        }
        if args.get("recurrence"):
            body["recurrence"] = args["recurrence"]
        try:
            data = await get_client().put("/maintenanceschedules", body)
            return json.dumps(data if data else {"status": "ok"}, indent=2)
        except Exception as e:
            return format_error(e)

    async def delete_maintenance_schedule(args: dict) -> str:
        if (g := write_guard()):
            return g
        ids = args.get("ids")
        if not ids:
            return json.dumps({"error": "Missing required argument: ids"})
        try:
            data = await get_client().delete("/maintenanceschedules", {"ids": ids})
            return json.dumps(data if data else {"status": "deleted"}, indent=2)
        except Exception as e:
            return format_error(e)

    # ── Reports ───────────────────────────────────────────────────────────────

    async def generate_report(args: dict) -> str:
        if (g := write_guard()):
            return g
        for field in ("reportDefinitionId", "resourceId"):
            if not args.get(field):
                return json.dumps({"error": f"Missing required argument: {field}"})
        body: dict[str, Any] = {
            "reportDefinitionId": args["reportDefinitionId"],
            "resourceId": args["resourceId"],
        }
        try:
            data = await get_client().post("/reports", body)
            return json.dumps(data, indent=2)
        except Exception as e:
            return format_error(e)

    async def delete_report(args: dict) -> str:
        if (g := write_guard()):
            return g
        report_id = args.get("id")
        if not report_id:
            return json.dumps({"error": "Missing required argument: id"})
        try:
            data = await get_client().delete(f"/reports/{quote(report_id, safe='')}")
            return json.dumps(data if data else {"status": "deleted"}, indent=2)
        except Exception as e:
            return format_error(e)

    async def create_report_schedule(args: dict) -> str:
        if (g := write_guard()):
            return g
        for field in ("reportDefinitionId", "resourceIds", "recurrence"):
            if not args.get(field):
                return json.dumps({"error": f"Missing required argument: {field}"})
        def_id = quote(args["reportDefinitionId"], safe="")
        body: dict[str, Any] = {
            "resourceIds": args["resourceIds"],
            "recurrence": args["recurrence"],
        }
        if args.get("emailConfig"):
            body["emailConfig"] = args["emailConfig"]
        try:
            data = await get_client().post(f"/reportdefinitions/{def_id}/schedules", body)
            return json.dumps(data, indent=2)
        except Exception as e:
            return format_error(e)

    async def update_report_schedule(args: dict) -> str:
        if (g := write_guard()):
            return g
        for field in ("reportDefinitionId", "scheduleId", "resourceIds", "recurrence"):
            if not args.get(field):
                return json.dumps({"error": f"Missing required argument: {field}"})
        def_id = quote(args["reportDefinitionId"], safe="")
        body: dict[str, Any] = {
            "id": args["scheduleId"],
            "resourceIds": args["resourceIds"],
            "recurrence": args["recurrence"],
        }
        if args.get("emailConfig"):
            body["emailConfig"] = args["emailConfig"]
        try:
            data = await get_client().put(f"/reportdefinitions/{def_id}/schedules", body)
            return json.dumps(data if data else {"status": "ok"}, indent=2)
        except Exception as e:
            return format_error(e)

    async def delete_report_schedule(args: dict) -> str:
        if (g := write_guard()):
            return g
        for field in ("reportDefinitionId", "scheduleId"):
            if not args.get(field):
                return json.dumps({"error": f"Missing required argument: {field}"})
        def_id = quote(args["reportDefinitionId"], safe="")
        sched_id = quote(args["scheduleId"], safe="")
        try:
            data = await get_client().delete(f"/reportdefinitions/{def_id}/schedules/{sched_id}")
            return json.dumps(data if data else {"status": "deleted"}, indent=2)
        except Exception as e:
            return format_error(e)

    # ── Resources ─────────────────────────────────────────────────────────────

    async def create_resource(args: dict) -> str:
        if (g := write_guard()):
            return g
        for field in ("adapterKindKey", "resourceKindKey", "resourceIdentifiers"):
            if not args.get(field):
                return json.dumps({"error": f"Missing required argument: {field}"})
        adapter_kind = quote(args["adapterKindKey"], safe="")
        body: dict[str, Any] = {
            "resourceKey": {
                "adapterKindKey": args["adapterKindKey"],
                "resourceKindKey": args["resourceKindKey"],
                "resourceIdentifiers": args["resourceIdentifiers"],
            }
        }
        if args.get("resourceName"):
            body["resourceKey"]["name"] = args["resourceName"]

        if args.get("adapterInstanceId"):
            instance_id = quote(args["adapterInstanceId"], safe="")
            path = f"/resources/adapters/{instance_id}"
        else:
            path = f"/resources/adapterkinds/{adapter_kind}"

        try:
            data = await get_client().post(path, body)
            return json.dumps(data, indent=2)
        except Exception as e:
            return format_error(e)

    async def update_resource(args: dict) -> str:
        if (g := write_guard()):
            return g
        resource = args.get("resource")
        if not resource or not isinstance(resource, dict):
            return json.dumps({"error": "Missing required argument: resource (must be an object)"})
        try:
            data = await get_client().put("/resources", resource)
            return json.dumps(data if data else {"status": "ok"}, indent=2)
        except Exception as e:
            return format_error(e)

    async def delete_resources(args: dict) -> str:
        if (g := write_guard()):
            return g
        resource_ids = args.get("resourceIds")
        if not resource_ids:
            return json.dumps({"error": "Missing required argument: resourceIds"})
        try:
            data = await get_client().post("/resources/bulk/delete", {"resourceIds": resource_ids})
            return json.dumps(data if data else {"status": "deleted"}, indent=2)
        except Exception as e:
            return format_error(e)

    return {
        "modify_alerts": modify_alerts,
        "add_alert_note": add_alert_note,
        "delete_alert_note": delete_alert_note,
        "delete_canceled_alerts": delete_canceled_alerts,
        "mark_resources_maintained": mark_resources_maintained,
        "unmark_resources_maintained": unmark_resources_maintained,
        "create_maintenance_schedule": create_maintenance_schedule,
        "update_maintenance_schedule": update_maintenance_schedule,
        "delete_maintenance_schedule": delete_maintenance_schedule,
        "generate_report": generate_report,
        "delete_report": delete_report,
        "create_report_schedule": create_report_schedule,
        "update_report_schedule": update_report_schedule,
        "delete_report_schedule": delete_report_schedule,
        "create_resource": create_resource,
        "update_resource": update_resource,
        "delete_resources": delete_resources,
    }
