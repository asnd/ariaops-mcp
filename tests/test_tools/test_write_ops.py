"""Tests for write operation tools."""

import json

import httpx
import pytest
import respx

from ariaops_mcp.tools.write_ops import tool_definitions, tool_handlers
from tests.conftest import TOKEN_RESPONSE

BASE = "https://vrops.test.local/suite-api/api"


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def handlers(mock_env, monkeypatch):
    """Handlers with writes ENABLED."""
    monkeypatch.setenv("ARIAOPS_ENABLE_WRITE_OPERATIONS", "true")
    return tool_handlers()


@pytest.fixture
def handlers_disabled(mock_env):
    """Handlers with writes DISABLED (default)."""
    return tool_handlers()


def _token_mock():
    respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))


# ── gate tests (writes disabled) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_writes_disabled_gate_modify_alerts(handlers_disabled):
    result = json.loads(await handlers_disabled["modify_alerts"]({"alertIds": ["a1"], "action": "CANCEL"}))
    assert "disabled" in result["error"].lower()
    assert "ARIAOPS_ENABLE_WRITE_OPERATIONS" in result["detail"]


@pytest.mark.asyncio
async def test_writes_disabled_gate_add_alert_note(handlers_disabled):
    result = json.loads(await handlers_disabled["add_alert_note"]({"id": "a1", "note": "hi"}))
    assert "disabled" in result["error"].lower()


@pytest.mark.asyncio
async def test_writes_disabled_gate_delete_resources(handlers_disabled):
    result = json.loads(await handlers_disabled["delete_resources"]({"resourceIds": ["r1"]}))
    assert "disabled" in result["error"].lower()


# ── server registry gating ────────────────────────────────────────────────────


def test_write_tools_absent_from_server_when_disabled(mock_env):
    from ariaops_mcp.server import _build_registry

    defs, _ = _build_registry()
    names = {t.name for t in defs}
    assert "modify_alerts" not in names
    assert "delete_resources" not in names


def test_write_tools_present_when_enabled(mock_env, monkeypatch):
    monkeypatch.setenv("ARIAOPS_ENABLE_WRITE_OPERATIONS", "true")
    from ariaops_mcp.config import get_settings
    get_settings.cache_clear()
    from ariaops_mcp.server import _build_registry

    defs, _ = _build_registry()
    names = {t.name for t in defs}
    assert "modify_alerts" in names
    assert "add_alert_note" in names
    assert "delete_resources" in names
    assert len([t for t in defs if t.name in {td.name for td in tool_definitions()}]) == 17


# ── tool_definitions completeness ────────────────────────────────────────────


def test_tool_definitions_count():
    defs = tool_definitions()
    assert len(defs) == 17
    names = {t.name for t in defs}
    expected = {
        "modify_alerts", "add_alert_note", "delete_alert_note", "delete_canceled_alerts",
        "mark_resources_maintained", "unmark_resources_maintained",
        "create_maintenance_schedule", "update_maintenance_schedule", "delete_maintenance_schedule",
        "generate_report", "delete_report",
        "create_report_schedule", "update_report_schedule", "delete_report_schedule",
        "create_resource", "update_resource", "delete_resources",
    }
    assert names == expected


# ── Alerts ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_modify_alerts_cancel(handlers):
    with respx.mock:
        _token_mock()
        respx.post(f"{BASE}/alerts").mock(return_value=httpx.Response(200, json={"alerts": []}))

        result = json.loads(await handlers["modify_alerts"]({"alertIds": ["a1", "a2"], "action": "CANCEL"}))
        assert "alerts" in result


@pytest.mark.asyncio
async def test_modify_alerts_invalid_action(handlers):
    result = json.loads(await handlers["modify_alerts"]({"alertIds": ["a1"], "action": "EXPLODE"}))
    assert "error" in result
    assert "EXPLODE" in result["error"]


@pytest.mark.asyncio
async def test_modify_alerts_missing_ids(handlers):
    result = json.loads(await handlers["modify_alerts"]({"action": "CANCEL"}))
    assert "error" in result
    assert "alertIds" in result["error"]


@pytest.mark.asyncio
async def test_modify_alerts_acknowledge(handlers):
    with respx.mock:
        _token_mock()
        respx.post(f"{BASE}/alerts").mock(return_value=httpx.Response(200, json={"alerts": []}))

        result = json.loads(await handlers["modify_alerts"]({"alertIds": ["a1"], "action": "ACKNOWLEDGE"}))
        assert "alerts" in result


@pytest.mark.asyncio
async def test_add_alert_note_success(handlers):
    with respx.mock:
        _token_mock()
        respx.post(f"{BASE}/alerts/alert-1/notes").mock(
            return_value=httpx.Response(200, json={"id": "note-1", "note": "test note"})
        )

        result = json.loads(await handlers["add_alert_note"]({"id": "alert-1", "note": "test note"}))
        assert result["id"] == "note-1"


@pytest.mark.asyncio
async def test_add_alert_note_missing_id(handlers):
    result = json.loads(await handlers["add_alert_note"]({"note": "hello"}))
    assert "id" in result["error"]


@pytest.mark.asyncio
async def test_add_alert_note_empty_note(handlers):
    result = json.loads(await handlers["add_alert_note"]({"id": "a1", "note": "   "}))
    assert "error" in result
    assert "empty" in result["error"].lower()


@pytest.mark.asyncio
async def test_add_alert_note_control_characters(handlers):
    result = json.loads(await handlers["add_alert_note"]({"id": "a1", "note": "bad\x01note"}))
    assert "error" in result
    assert "control" in result["error"].lower()


@pytest.mark.asyncio
async def test_add_alert_note_too_long(handlers):
    result = json.loads(await handlers["add_alert_note"]({"id": "a1", "note": "x" * 4001}))
    assert "error" in result
    assert "4000" in result["error"]


@pytest.mark.asyncio
async def test_delete_alert_note_success(handlers):
    with respx.mock:
        _token_mock()
        respx.delete(f"{BASE}/alerts/a1/notes/n1").mock(return_value=httpx.Response(204))

        result = json.loads(await handlers["delete_alert_note"]({"id": "a1", "noteId": "n1"}))
        assert result.get("status") == "deleted"


@pytest.mark.asyncio
async def test_delete_alert_note_missing_fields(handlers):
    result = json.loads(await handlers["delete_alert_note"]({"id": "a1"}))
    assert "noteId" in result["error"]


@pytest.mark.asyncio
async def test_delete_alert_note_url_encoding(handlers):
    with respx.mock:
        _token_mock()
        route = respx.delete(f"{BASE}/alerts/a%2F1/notes/n%2F2").mock(return_value=httpx.Response(204))

        await handlers["delete_alert_note"]({"id": "a/1", "noteId": "n/2"})
        assert route.called


@pytest.mark.asyncio
async def test_delete_canceled_alerts(handlers):
    with respx.mock:
        _token_mock()
        respx.post(f"{BASE}/alerts/bulk/delete").mock(return_value=httpx.Response(204))

        result = json.loads(await handlers["delete_canceled_alerts"](
            {"alertIds": ["a1"], "olderThanDays": 7}
        ))
        assert result.get("status") == "deleted"


@pytest.mark.asyncio
async def test_delete_canceled_alerts_http_error(handlers):
    with respx.mock:
        _token_mock()
        respx.post(f"{BASE}/alerts/bulk/delete").mock(
            return_value=httpx.Response(404, json={"message": "not found"})
        )

        result = json.loads(await handlers["delete_canceled_alerts"]({}))
        assert "error" in result
        assert result["status_code"] == 404


# ── Resource maintenance ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_resources_maintained(handlers):
    with respx.mock:
        _token_mock()
        respx.put(f"{BASE}/resources/maintained").mock(return_value=httpx.Response(200, json={"status": "ok"}))

        result = json.loads(await handlers["mark_resources_maintained"]({"resourceIds": ["r1"]}))
        assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_mark_resources_maintained_missing_ids(handlers):
    result = json.loads(await handlers["mark_resources_maintained"]({}))
    assert "resourceIds" in result["error"]


@pytest.mark.asyncio
async def test_unmark_resources_maintained(handlers):
    with respx.mock:
        _token_mock()
        respx.delete(f"{BASE}/resources/maintained").mock(return_value=httpx.Response(204))

        result = json.loads(await handlers["unmark_resources_maintained"]({"resourceIds": ["r1"]}))
        assert result.get("status") == "ok"


# ── Maintenance schedules ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_maintenance_schedule(handlers):
    with respx.mock:
        _token_mock()
        respx.post(f"{BASE}/maintenanceschedules").mock(
            return_value=httpx.Response(200, json={"id": "sched-1", "name": "test"})
        )

        result = json.loads(await handlers["create_maintenance_schedule"]({
            "name": "test",
            "resourceIds": ["r1"],
            "startTime": 1000000,
            "endTime": 2000000,
        }))
        assert result["id"] == "sched-1"


@pytest.mark.asyncio
async def test_create_maintenance_schedule_missing_fields(handlers):
    result = json.loads(await handlers["create_maintenance_schedule"]({"name": "test"}))
    assert "error" in result


@pytest.mark.asyncio
async def test_update_maintenance_schedule(handlers):
    with respx.mock:
        _token_mock()
        respx.put(f"{BASE}/maintenanceschedules").mock(return_value=httpx.Response(204))

        result = json.loads(await handlers["update_maintenance_schedule"]({
            "id": "sched-1",
            "name": "updated",
            "resourceIds": ["r1"],
            "startTime": 1000000,
            "endTime": 2000000,
        }))
        assert result.get("status") == "ok"


@pytest.mark.asyncio
async def test_delete_maintenance_schedule(handlers):
    with respx.mock:
        _token_mock()
        respx.delete(f"{BASE}/maintenanceschedules").mock(return_value=httpx.Response(204))

        result = json.loads(await handlers["delete_maintenance_schedule"]({"ids": ["sched-1"]}))
        assert result.get("status") == "deleted"


# ── Reports ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_report(handlers):
    with respx.mock:
        _token_mock()
        respx.post(f"{BASE}/reports").mock(
            return_value=httpx.Response(200, json={"id": "rpt-1", "status": "RUNNING"})
        )

        result = json.loads(await handlers["generate_report"]({
            "reportDefinitionId": "def-1",
            "resourceId": "res-1",
        }))
        assert result["id"] == "rpt-1"


@pytest.mark.asyncio
async def test_generate_report_missing_args(handlers):
    result = json.loads(await handlers["generate_report"]({"reportDefinitionId": "def-1"}))
    assert "resourceId" in result["error"]


@pytest.mark.asyncio
async def test_delete_report(handlers):
    with respx.mock:
        _token_mock()
        respx.delete(f"{BASE}/reports/rpt-1").mock(return_value=httpx.Response(204))

        result = json.loads(await handlers["delete_report"]({"id": "rpt-1"}))
        assert result.get("status") == "deleted"


@pytest.mark.asyncio
async def test_create_report_schedule(handlers):
    with respx.mock:
        _token_mock()
        respx.post(f"{BASE}/reportdefinitions/def-1/schedules").mock(
            return_value=httpx.Response(200, json={"id": "sched-1"})
        )

        result = json.loads(await handlers["create_report_schedule"]({
            "reportDefinitionId": "def-1",
            "resourceIds": ["res-1"],
            "recurrence": "FREQ=WEEKLY;BYDAY=MO",
        }))
        assert result["id"] == "sched-1"


@pytest.mark.asyncio
async def test_update_report_schedule(handlers):
    with respx.mock:
        _token_mock()
        respx.put(f"{BASE}/reportdefinitions/def-1/schedules").mock(return_value=httpx.Response(204))

        result = json.loads(await handlers["update_report_schedule"]({
            "reportDefinitionId": "def-1",
            "scheduleId": "sched-1",
            "resourceIds": ["res-1"],
            "recurrence": "FREQ=WEEKLY;BYDAY=MO",
        }))
        assert result.get("status") == "ok"


@pytest.mark.asyncio
async def test_delete_report_schedule(handlers):
    with respx.mock:
        _token_mock()
        respx.delete(f"{BASE}/reportdefinitions/def-1/schedules/sched-1").mock(
            return_value=httpx.Response(204)
        )

        result = json.loads(await handlers["delete_report_schedule"]({
            "reportDefinitionId": "def-1",
            "scheduleId": "sched-1",
        }))
        assert result.get("status") == "deleted"


# ── Resources ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_resource_via_adapter_kind(handlers):
    with respx.mock:
        _token_mock()
        respx.post(f"{BASE}/resources/adapterkinds/VMWARE").mock(
            return_value=httpx.Response(200, json={"resourceKey": {"adapterKindKey": "VMWARE"}})
        )

        result = json.loads(await handlers["create_resource"]({
            "adapterKindKey": "VMWARE",
            "resourceKindKey": "VirtualMachine",
            "resourceIdentifiers": [{"identifierType": {"name": "VMEntityName"}, "value": "vm01"}],
        }))
        assert result["resourceKey"]["adapterKindKey"] == "VMWARE"


@pytest.mark.asyncio
async def test_create_resource_via_adapter_instance(handlers):
    with respx.mock:
        _token_mock()
        respx.post(f"{BASE}/resources/adapters/inst-1").mock(
            return_value=httpx.Response(200, json={"id": "new-res"})
        )

        result = json.loads(await handlers["create_resource"]({
            "adapterKindKey": "VMWARE",
            "resourceKindKey": "VirtualMachine",
            "resourceIdentifiers": [{"identifierType": {"name": "VMEntityName"}, "value": "vm01"}],
            "adapterInstanceId": "inst-1",
        }))
        assert result["id"] == "new-res"


@pytest.mark.asyncio
async def test_create_resource_missing_fields(handlers):
    result = json.loads(await handlers["create_resource"]({"adapterKindKey": "VMWARE"}))
    assert "error" in result


@pytest.mark.asyncio
async def test_update_resource(handlers):
    resource_obj = {"identifier": "res-1", "resourceKey": {"adapterKindKey": "VMWARE"}}
    with respx.mock:
        _token_mock()
        respx.put(f"{BASE}/resources").mock(return_value=httpx.Response(200, json=resource_obj))

        result = json.loads(await handlers["update_resource"]({"resource": resource_obj}))
        assert result["identifier"] == "res-1"


@pytest.mark.asyncio
async def test_update_resource_missing_body(handlers):
    result = json.loads(await handlers["update_resource"]({}))
    assert "resource" in result["error"]


@pytest.mark.asyncio
async def test_update_resource_non_dict_body(handlers):
    result = json.loads(await handlers["update_resource"]({"resource": "not-an-object"}))
    assert "error" in result


@pytest.mark.asyncio
async def test_delete_resources(handlers):
    with respx.mock:
        _token_mock()
        respx.post(f"{BASE}/resources/bulk/delete").mock(return_value=httpx.Response(204))

        result = json.loads(await handlers["delete_resources"]({"resourceIds": ["r1", "r2"]}))
        assert result.get("status") == "deleted"


@pytest.mark.asyncio
async def test_delete_resources_missing_ids(handlers):
    result = json.loads(await handlers["delete_resources"]({}))
    assert "resourceIds" in result["error"]


@pytest.mark.asyncio
async def test_delete_resources_network_error(handlers):
    with respx.mock:
        _token_mock()
        respx.post(f"{BASE}/resources/bulk/delete").mock(side_effect=httpx.ConnectError("timeout"))

        result = json.loads(await handlers["delete_resources"]({"resourceIds": ["r1"]}))
        assert "error" in result
        assert "network" in result["error"].lower()
