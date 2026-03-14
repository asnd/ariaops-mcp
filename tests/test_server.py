"""Tests for server-level tool argument validation."""

from ariaops_mcp.server import _is_missing_required_argument, _missing_required_arguments
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
    tool = next(tool for tool in resources.tool_definitions() if tool.name == "get_resource")
    assert _missing_required_arguments(tool, {"id": "   "}) == ["id"]
    assert _missing_required_arguments(tool, {"id": "vm-001"}) == []


def test_missing_required_arguments_for_list_required_field():
    tool = next(tool for tool in metrics.tool_definitions() if tool.name == "query_stats")
    assert _missing_required_arguments(
        tool, {"resourceIds": [" "], "statKeys": ["cpu|usage_average"]}
    ) == ["resourceIds"]
    assert _missing_required_arguments(tool, {"resourceIds": ["vm-001"], "statKeys": ["cpu|usage_average"]}) == []
