"""Tests for meta-tools (list_instances, select_instance, instance_health, query_all_instances)."""

import json
from unittest.mock import AsyncMock

import pytest

from ariaops_mcp.instances import reset_instance_registry
from ariaops_mcp.resolver import clear_session_instance, get_session_instance
from ariaops_mcp.tools.meta import (
    _handle_instance_health,
    _handle_list_instances,
    _handle_query_all_instances,
    _handle_select_instance,
)


@pytest.fixture
def multi_instance_setup(monkeypatch, tmp_path):
    """Set up a multi-instance registry for meta-tool tests."""
    yaml_content = """
default_instance: prod
broadcast_timeout_seconds: 10
broadcast_max_instances: 5
instances:
  prod:
    host: prod.example.com
    environment: production
    write_enabled: false
    description: "Production SE"
    labels:
      countries: [SE, DK]
  prod-fi:
    host: prod-fi.example.com
    environment: production
    write_enabled: false
    description: "Production FI"
    labels:
      countries: [FI]
  testbed:
    host: lab.example.com
    environment: testbed
    write_enabled: true
    description: "Lab environment"
"""
    config_file = tmp_path / "instances.yaml"
    config_file.write_text(yaml_content)
    monkeypatch.setenv("ARIAOPS_INSTANCES_FILE", str(config_file))
    monkeypatch.setenv("ARIAOPS_USERNAME", "testuser")
    monkeypatch.setenv("ARIAOPS_PASSWORD", "testpass")
    reset_instance_registry()
    yield
    clear_session_instance()


# ── list_instances Tests ──────────────────────────────────────────────────────


class TestListInstances:
    async def test_list_all(self, multi_instance_setup):
        result = json.loads(await _handle_list_instances({}))
        assert result["total"] == 3
        names = [i["name"] for i in result["instances"]]
        assert "prod" in names
        assert "prod-fi" in names
        assert "testbed" in names

    async def test_filter_by_environment(self, multi_instance_setup):
        result = json.loads(await _handle_list_instances({"environment": "production"}))
        assert result["total"] == 2
        names = [i["name"] for i in result["instances"]]
        assert "prod" in names
        assert "prod-fi" in names
        assert "testbed" not in names

    async def test_shows_server_default(self, multi_instance_setup):
        result = json.loads(await _handle_list_instances({}))
        assert result["server_default"] == "prod"

    async def test_shows_session_default(self, multi_instance_setup):
        from ariaops_mcp.resolver import set_session_instance
        set_session_instance("testbed")
        result = json.loads(await _handle_list_instances({}))
        assert result["session_default"] == "testbed"
        clear_session_instance()


# ── select_instance Tests ─────────────────────────────────────────────────────


class TestSelectInstance:
    async def test_select_valid(self, multi_instance_setup):
        result = json.loads(await _handle_select_instance({"instance": "prod-fi"}))
        assert result["status"] == "ok"
        assert result["instance"]["name"] == "prod-fi"
        assert get_session_instance() == "prod-fi"

    async def test_select_invalid(self, multi_instance_setup):
        result = json.loads(await _handle_select_instance({"instance": "nonexistent"}))
        assert "error" in result
        assert "available" in result

    async def test_select_missing_arg(self, multi_instance_setup):
        result = json.loads(await _handle_select_instance({}))
        assert "error" in result


# ── instance_health Tests ─────────────────────────────────────────────────────


class TestInstanceHealth:
    async def test_health_single_not_initialized(self, multi_instance_setup):
        result = json.loads(await _handle_instance_health({"instance": "prod"}))
        assert result["status"] == "not_initialized"
        assert result["instance"] == "prod"

    async def test_health_all(self, multi_instance_setup):
        result = json.loads(await _handle_instance_health({}))
        assert result["total"] == 3

    async def test_health_nonexistent(self, multi_instance_setup):
        result = json.loads(await _handle_instance_health({"instance": "nonexistent"}))
        assert "error" in result


# ── query_all_instances Tests ─────────────────────────────────────────────────


class TestQueryAllInstances:
    async def test_rejects_write_tool(self, multi_instance_setup):
        write_tools = {"modify_alerts"}
        handlers = {"list_alerts": AsyncMock(return_value='{"alerts": []}')}
        result = json.loads(await _handle_query_all_instances(
            {"tool_name": "modify_alerts", "tool_args": {}},
            handlers,
            write_tools,
        ))
        assert "error" in result
        assert "write operation" in result["error"]

    async def test_rejects_unknown_tool(self, multi_instance_setup):
        result = json.loads(await _handle_query_all_instances(
            {"tool_name": "nonexistent_tool", "tool_args": {}},
            {"list_alerts": AsyncMock()},
            set(),
        ))
        assert "error" in result
        assert "not found" in result["error"]

    async def test_missing_tool_name(self, multi_instance_setup):
        result = json.loads(await _handle_query_all_instances(
            {"tool_args": {}},
            {},
            set(),
        ))
        assert "error" in result

    async def test_broadcasts_to_filtered_instances(self, multi_instance_setup):
        mock_handler = AsyncMock(return_value='{"alerts": []}')
        handlers = {"list_alerts": mock_handler}

        result = json.loads(await _handle_query_all_instances(
            {
                "tool_name": "list_alerts",
                "tool_args": {"status": "ACTIVE"},
                "filter_environment": "production",
            },
            handlers,
            set(),
        ))

        assert result["tool"] == "list_alerts"
        assert result["summary"]["queried"] == 2  # prod + prod-fi
        assert "prod" in result["results"]
        assert "prod-fi" in result["results"]
        assert "testbed" not in result["results"]

    async def test_handles_timeout_gracefully(self, multi_instance_setup):
        """Tool that times out should be reported but not fail the whole query."""
        import asyncio

        async def slow_handler(args):
            await asyncio.sleep(100)  # Will be cancelled by timeout
            return '{"data": "never returned"}'

        handlers = {"slow_tool": slow_handler}

        result = json.loads(await _handle_query_all_instances(
            {
                "tool_name": "slow_tool",
                "tool_args": {},
                "filter_environment": "testbed",
            },
            handlers,
            set(),
        ))

        assert result["summary"]["queried"] == 1
        assert result["summary"]["failed"] == 1
        testbed_result = result["results"]["testbed"]
        assert "timeout" in testbed_result.get("error", "") or testbed_result.get("status") == "timeout"

    async def test_no_matching_instances(self, multi_instance_setup):
        result = json.loads(await _handle_query_all_instances(
            {
                "tool_name": "list_alerts",
                "tool_args": {},
                "filter_labels": {"countries": ["JP"]},
            },
            {"list_alerts": AsyncMock()},
            set(),
        ))
        assert "error" in result
        assert "No instances match" in result["error"]
