"""Tests for server-level tool argument validation."""

from ariaops_mcp.server import _build_registry, _is_missing_required_argument, _missing_required_arguments
from ariaops_mcp.tools import metrics, resources


def test_missing_required_argument_corner_cases():
    assert _is_missing_required_argument(None)
    assert _is_missing_required_argument("")
    assert _is_missing_required_argument("   ")
    assert _is_missing_required_argument([])
    assert _is_missing_required_argument(["", "  "])
    assert not _is_missing_required_argument("vm-001")
    assert not _is_missing_required_argument([" ", "vm-001"])


def test_missing_required_arguments_for_string_required_field():
    tool = next((tool for tool in resources.tool_definitions() if tool.name == "get_resource"), None)
    assert tool is not None, "get_resource tool not found in resources.tool_definitions()"
    assert _missing_required_arguments(tool, {"id": "   "}) == ["id"]
    assert _missing_required_arguments(tool, {"id": "vm-001"}) == []


def test_missing_required_arguments_for_list_required_field():
    tool = next((tool for tool in metrics.tool_definitions() if tool.name == "query_stats"), None)
    assert tool is not None, "query_stats tool not found in metrics.tool_definitions()"
    assert _missing_required_arguments(
        tool, {"resourceIds": [" "], "statKeys": ["cpu|usage_average"]}
    ) == ["resourceIds"]
    assert _missing_required_arguments(tool, {"resourceIds": ["vm-001"], "statKeys": ["cpu|usage_average"]}) == []


def test_registry_excludes_write_tools_by_default():
    defs, handlers = _build_registry()
    names = {tool.name for tool in defs}
    assert "add_alert_note" not in names
    assert "set_alert_status" not in names
    assert "add_alert_note" not in handlers
    assert "set_alert_status" not in handlers


def test_registry_includes_write_tools_when_enabled():
    defs, handlers = _build_registry(include_write_operations=True)
    names = {tool.name for tool in defs}
    assert "add_alert_note" in names
    assert "set_alert_status" in names
    assert "add_alert_note" in handlers
    assert "set_alert_status" in handlers
