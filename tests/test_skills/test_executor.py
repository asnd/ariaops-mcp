"""Tests for skills/executor.py."""

import asyncio
import json

import pytest

from ariaops_mcp.skills.executor import execute_skill
from ariaops_mcp.skills.models import Skill, SkillArgument, SkillStep


async def _mock_handler(args: dict) -> str:
    return json.dumps({"id": args.get("id", "unknown"), "status": "ok"})


async def _mock_nested_handler(args: dict) -> str:
    """Returns a nested dict to test deep field access."""
    return json.dumps({"resource": {"name": "host-01", "type": "virtual", "tags": ["prod"]}})


async def _failing_handler(args: dict) -> str:
    raise RuntimeError("Tool failed")


async def _slow_handler(args: dict) -> str:
    await asyncio.sleep(2.0)
    return json.dumps({"done": True})


async def _echo_handler(args: dict) -> str:
    return json.dumps(args)


async def _numeric_handler(args: dict) -> str:
    """Returns raw numbers that should NOT be JSON-parsed as scalars."""
    return json.dumps({"count": "42", "enabled": "true"})


class TestExecuteSkillBasics:
    @pytest.mark.asyncio
    async def test_single_step_success(self):
        skill = Skill(
            name="test",
            description="Test",
            tools=["get_alert"],
            orchestration=True,
            steps=[SkillStep(tool="get_alert", args_template={"id": "{{alert_id}}"}, output_key="alert")],
        )
        handlers = {"get_alert": _mock_handler}

        result = await execute_skill(skill, {"alert_id": "ALERT-1"}, handlers)
        assert result["status"] == "completed"
        assert len(result["steps"]) == 1
        assert result["steps"][0]["status"] == "success"

    @pytest.mark.asyncio
    async def test_argument_with_hyphens(self):
        """Arguments with hyphens should resolve correctly (#3)."""
        skill = Skill(
            name="hyphen-test",
            description="Test",
            tools=["get_alert"],
            orchestration=True,
            steps=[SkillStep(tool="get_alert", args_template={"id": "{{my-arg}}"}, output_key="alert")],
        )
        handlers = {"get_alert": _mock_handler}

        result = await execute_skill(skill, {"my-arg": "HYPHEN-1"}, handlers)
        assert result["status"] == "completed"
        assert result["steps"][0]["status"] == "success"

    @pytest.mark.asyncio
    async def test_no_orchestration(self):
        skill = Skill(name="info", description="Info only", orchestration=False)
        result = await execute_skill(skill, {}, {})
        assert result["status"] == "error"
        assert "does not support orchestration" in result["error"]

    @pytest.mark.asyncio
    async def test_no_steps(self):
        skill = Skill(name="empty", description="Empty", tools=["get_alert"], orchestration=True, steps=[])
        result = await execute_skill(skill, {}, {})
        assert result["status"] == "error"


class TestOutputChaining:
    @pytest.mark.asyncio
    async def test_chaining_with_nested_field(self):
        """Output chaining with nested fields like {{steps.0.alert.id}} should work (#1)."""
        skill = Skill(
            name="chain",
            description="Chain test",
            tools=["get_alert", "get_resource"],
            orchestration=True,
            steps=[
                SkillStep(tool="get_alert", args_template={"id": "{{alert_id}}"}, output_key="alert"),
                SkillStep(
                    tool="get_resource",
                    args_template={"id": "{{steps.0.alert.id}}"},
                    output_key="resource",
                ),
            ],
        )
        handlers = {"get_alert": _mock_handler, "get_resource": _mock_handler}

        result = await execute_skill(skill, {"alert_id": "ALERT-1"}, handlers)
        assert result["status"] == "completed"
        assert len(result["steps"]) == 2
        assert result["steps"][0]["status"] == "success"
        assert result["steps"][1]["status"] == "success"

    @pytest.mark.asyncio
    async def test_deeply_nested_field_access(self):
        """Access deeply nested fields: {{steps.0.data.resource.name}}."""
        skill = Skill(
            name="deep",
            description="Deep nesting test",
            tools=["get_data", "get_resource"],
            orchestration=True,
            steps=[
                SkillStep(tool="get_data", args_template={}, output_key="data"),
                SkillStep(
                    tool="get_resource",
                    args_template={"name": "{{steps.0.data.resource.name}}"},
                    output_key="resource",
                ),
            ],
        )
        handlers = {"get_data": _mock_nested_handler, "get_resource": _mock_handler}

        result = await execute_skill(skill, {}, handlers)
        assert result["status"] == "completed"
        assert result["steps"][0]["status"] == "success"
        assert result["steps"][1]["status"] == "success"

    @pytest.mark.asyncio
    async def test_dependency_on_failed_step_skips_downstream(self):
        """When step 0 fails, step 1 referencing steps.0.* should be skipped (not error)."""
        skill = Skill(
            name="dep-fail",
            description="Dependency failure test",
            tools=["fail_tool", "get_resource"],
            orchestration=True,
            steps=[
                SkillStep(tool="fail_tool", args_template={}, output_key="bad"),
                SkillStep(tool="get_resource", args_template={"id": "{{steps.0.result}}"}, output_key="resource"),
            ],
        )
        handlers = {"fail_tool": _failing_handler, "get_resource": _mock_handler}

        result = await execute_skill(skill, {}, handlers)
        assert result["status"] == "failed"
        assert result["steps"][0]["status"] == "error"
        assert result["steps"][1]["status"] == "skipped"
        assert "unresolved dependency" in result["steps"][1]["error"]

    @pytest.mark.asyncio
    async def test_out_of_bounds_step_index(self):
        """A step referencing steps.99.field should be skipped."""
        skill = Skill(
            name="oob",
            description="Out-of-bounds step ref",
            tools=["get_alert"],
            orchestration=True,
            steps=[SkillStep(tool="get_alert", args_template={"id": "{{steps.99.nope}}"})],
        )
        handlers = {"get_alert": _mock_handler}

        result = await execute_skill(skill, {}, handlers)
        assert result["steps"][0]["status"] == "skipped"
        assert "unresolved dependency" in result["steps"][0]["error"]

    @pytest.mark.asyncio
    async def test_mixed_templates(self):
        """Mixed {{arg}} + {{steps.N.field}} in one value should resolve."""
        skill = Skill(
            name="mixed",
            description="Mixed templates",
            tools=["get_data", "get_resource"],
            orchestration=True,
            steps=[
                SkillStep(tool="get_data", args_template={}, output_key="data"),
                SkillStep(
                    tool="get_resource",
                    args_template={"path": "/{{env}}/{{steps.0.data.resource.name}}"},
                    output_key="resource",
                ),
            ],
        )
        handlers = {"get_data": _mock_nested_handler, "get_resource": _mock_handler}

        result = await execute_skill(skill, {"env": "prod"}, handlers)
        assert result["status"] == "completed"


class TestBestEffort:
    @pytest.mark.asyncio
    async def test_independent_steps_continue_on_failure(self):
        """Steps that don't depend on a failed step should still execute."""
        skill = Skill(
            name="partial",
            description="Partial",
            tools=["fail_tool", "get_alert"],
            orchestration=True,
            steps=[
                SkillStep(tool="fail_tool", args_template={}, output_key="bad"),
                SkillStep(tool="get_alert", args_template={"id": "{{alert_id}}"}, output_key="alert"),
            ],
        )
        handlers = {"fail_tool": _failing_handler, "get_alert": _mock_handler}

        result = await execute_skill(skill, {"alert_id": "ALERT-1"}, handlers)
        assert result["status"] == "partial"
        assert result["steps"][0]["status"] == "error"
        assert result["steps"][1]["status"] == "success"

    @pytest.mark.asyncio
    async def test_all_steps_fail(self):
        skill = Skill(
            name="all-fail",
            description="All fail",
            tools=["fail_one", "fail_two"],
            orchestration=True,
            steps=[
                SkillStep(tool="fail_one", args_template={}),
                SkillStep(tool="fail_two", args_template={}),
            ],
        )
        handlers = {"fail_one": _failing_handler, "fail_two": _failing_handler}

        result = await execute_skill(skill, {}, handlers)
        assert result["status"] == "failed"
        assert all(s["status"] == "error" for s in result["steps"])


class TestJsonParseSafety:
    @pytest.mark.asyncio
    async def test_no_scalar_json_coercion(self):
        """Numeric/bool strings should NOT be JSON-parsed to scalars (#4)."""
        skill = Skill(
            name="numeric",
            description="Numeric strings",
            tools=["get_stats"],
            orchestration=True,
            steps=[SkillStep(tool="get_stats", args_template={}, output_key="stats")],
        )
        handlers = {"get_stats": _numeric_handler}

        result = await execute_skill(skill, {}, handlers)
        assert result["status"] == "completed"
        # The output from _numeric_handler has string values "42" and "true"
        # They must remain strings, not int/bool.
        output = result["steps"][0]["output"]
        assert isinstance(output["count"], str)
        assert isinstance(output["enabled"], str)
        assert output["count"] == "42"
        assert output["enabled"] == "true"

    @pytest.mark.asyncio
    async def test_json_args_parsed_correctly(self):
        """Args starting with { or [ should be JSON-parsed."""
        skill = Skill(
            name="json-args",
            description="JSON args",
            tools=["echo"],
            orchestration=True,
            steps=[SkillStep(tool="echo", args_template={"data": "[1,2,3]"})],
        )
        handlers = {"echo": _echo_handler}

        result = await execute_skill(skill, {}, handlers)
        assert result["status"] == "completed"
        output = result["steps"][0]["output"]
        assert isinstance(output["data"], list)
        assert output["data"] == [1, 2, 3]


class TestWriteGuard:
    @pytest.mark.asyncio
    async def test_write_tool_blocked_when_disabled(self):
        """Write tools should be blocked when write_enabled=False."""
        skill = Skill(
            name="write-skill",
            description="Uses write ops",
            tools=["get_alert", "delete_resources"],
            orchestration=True,
            steps=[
                SkillStep(tool="get_alert", args_template={"id": "{{alert_id}}"}, output_key="alert"),
                SkillStep(tool="delete_resources", args_template={"ids": "[\"r1\"]"}),
            ],
        )
        handlers = {"get_alert": _mock_handler, "delete_resources": _echo_handler}
        write_tools = {"delete_resources", "modify_alerts"}

        result = await execute_skill(
            skill, {"alert_id": "A1"}, handlers,
            write_enabled=False, write_tool_names=write_tools,
        )
        assert result["steps"][0]["status"] == "success"
        assert result["steps"][1]["status"] == "skipped"
        assert "write operations disabled" in result["steps"][1]["error"].lower()

    @pytest.mark.asyncio
    async def test_write_tool_allowed_when_enabled(self):
        """Write tools should be allowed when write_enabled=True."""
        skill = Skill(
            name="write-skill",
            description="Uses write ops",
            tools=["delete_resources"],
            orchestration=True,
            steps=[
                SkillStep(tool="delete_resources", args_template={"ids": "[\"r1\"]"}),
            ],
        )
        handlers = {"delete_resources": _echo_handler}
        write_tools = {"delete_resources"}

        result = await execute_skill(
            skill, {}, handlers,
            write_enabled=True, write_tool_names=write_tools,
        )
        assert result["steps"][0]["status"] == "success"

    @pytest.mark.asyncio
    async def test_write_tool_names_none_default(self):
        """When write_tool_names=None, the default set() fallback should work."""
        skill = Skill(
            name="safe-skill",
            description="Safe",
            tools=["get_alert"],
            orchestration=True,
            steps=[SkillStep(tool="get_alert", args_template={"id": "A1"})],
        )
        handlers = {"get_alert": _mock_handler}

        result = await execute_skill(
            skill, {}, handlers,
            write_enabled=False, write_tool_names=None,
        )
        assert result["status"] == "completed"


class TestUnknownToolHandling:
    @pytest.mark.asyncio
    async def test_unknown_tool_skipped(self):
        skill = Skill(
            name="unknown",
            description="Unknown tool",
            tools=["nonexistent-tool"],
            orchestration=True,
            steps=[SkillStep(tool="nonexistent-tool", args_template={})],
        )
        handlers = {}

        result = await execute_skill(skill, {}, handlers)
        assert result["status"] == "failed"
        assert result["steps"][0]["status"] == "skipped"


class TestRequiredArgumentsValidation:
    @pytest.mark.asyncio
    async def test_missing_required_args_returns_error(self):
        skill = Skill(
            name="needs-args",
            description="Needs args",
            arguments=[
                SkillArgument(name="alert_id", required=True),
                SkillArgument(name="depth", required=False),
            ],
            orchestration=True,
            tools=["get_alert"],
            steps=[SkillStep(tool="get_alert", args_template={"id": "{{alert_id}}"})],
        )

        result = await execute_skill(skill, {}, {})
        assert result["status"] == "error"
        assert "Missing required argument" in result["error"]
        assert "alert_id" in result["error"]

    @pytest.mark.asyncio
    async def test_all_required_args_present_proceeds(self):
        skill = Skill(
            name="has-args",
            description="Has args",
            arguments=[
                SkillArgument(name="alert_id", required=True),
            ],
            tools=["get_alert"],
            orchestration=True,
            steps=[SkillStep(tool="get_alert", args_template={"id": "{{alert_id}}"})],
        )

        result = await execute_skill(skill, {"alert_id": "A1"}, {"get_alert": _mock_handler})
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_multiple_missing_required_args(self):
        skill = Skill(
            name="multi-req",
            description="Multiple required",
            arguments=[
                SkillArgument(name="a", required=True),
                SkillArgument(name="b", required=True),
                SkillArgument(name="c", required=False),
            ],
            tools=["x"],
            orchestration=True,
            steps=[SkillStep(tool="x", args_template={})],
        )

        result = await execute_skill(skill, {"c": "ok"}, {})
        assert result["status"] == "error"
        assert "a" in result["error"]
        assert "b" in result["error"]


class TestToolAllowlist:
    @pytest.mark.asyncio
    async def test_only_declared_tools_allowed(self):
        skill = Skill(
            name="restricted",
            description="Restricted",
            tools=["get_alert"],
            orchestration=True,
            steps=[SkillStep(tool="get_alert", args_template={"id": "A1"})],
        )
        handlers = {
            "get_alert": _mock_handler,
            "delete_resources": _echo_handler,
            "get_resource": _mock_handler,
        }

        result = await execute_skill(skill, {}, handlers)
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_service_error(self):
        """When a handler raises an unexpected error, it should be caught."""
        skill = Skill(
            name="service-err",
            description="Service error",
            tools=["fail_tool"],
            orchestration=True,
            steps=[SkillStep(tool="fail_tool", args_template={})],
        )
        handlers = {"fail_tool": _failing_handler}

        result = await execute_skill(skill, {}, handlers)
        assert result["status"] == "failed"
        assert result["steps"][0]["status"] == "error"


class TestStepTimeout:
    @pytest.mark.asyncio
    async def test_step_timeout_caught(self):
        """A step that exceeds step_timeout should be marked as error."""
        skill = Skill(
            name="slow",
            description="Slow step",
            tools=["slow_tool"],
            orchestration=True,
            steps=[SkillStep(tool="slow_tool", args_template={})],
        )
        handlers = {"slow_tool": _slow_handler}

        result = await execute_skill(skill, {}, handlers, step_timeout=0.1)
        assert result["steps"][0]["status"] == "error"
        assert "timed out" in result["steps"][0]["error"].lower()

    @pytest.mark.asyncio
    async def test_step_within_timeout_succeeds(self):
        """A fast step within timeout should succeed."""
        skill = Skill(
            name="fast",
            description="Fast step",
            tools=["fast_tool"],
            orchestration=True,
            steps=[SkillStep(tool="fast_tool", args_template={})],
        )
        handlers = {"fast_tool": _mock_handler}

        result = await execute_skill(skill, {}, handlers, step_timeout=5.0)
        assert result["status"] == "completed"