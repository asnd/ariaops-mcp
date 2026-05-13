"""Tests for skills/executor.py."""

import json

import pytest

from ariaops_mcp.skills.executor import execute_skill
from ariaops_mcp.skills.models import Skill, SkillArgument, SkillStep


async def _mock_handler(args: dict) -> str:
    return json.dumps({"id": args.get("id", "unknown"), "status": "ok"})


async def _failing_handler(args: dict) -> str:
    raise RuntimeError("Tool failed")


async def _echo_handler(args: dict) -> str:
    return json.dumps(args)


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
    async def test_no_orchestration(self):
        skill = Skill(name="info", description="Info only", orchestration=False)
        result = await execute_skill(skill, {}, {})
        assert result["status"] == "error"
        assert "does not support orchestration" in result["error"]

    @pytest.mark.asyncio
    async def test_no_steps(self):
        skill = Skill(name="empty", description="Empty", orchestration=True, steps=[])
        result = await execute_skill(skill, {}, {})
        assert result["status"] == "error"


class TestOutputChaining:
    @pytest.mark.asyncio
    async def test_chaining_success(self):
        skill = Skill(
            name="chain",
            description="Chain test",
            tools=["get_alert", "get_resource"],
            orchestration=True,
            steps=[
                SkillStep(tool="get_alert", args_template={"id": "{{alert_id}}"}, output_key="alert"),
                SkillStep(tool="get_resource", args_template={"id": "{{steps.0.alert.id}}"}, output_key="resource"),
            ],
        )
        handlers = {"get_alert": _mock_handler, "get_resource": _mock_handler}

        result = await execute_skill(skill, {"alert_id": "ALERT-1"}, handlers)
        assert result["status"] == "completed"
        assert len(result["steps"]) == 2

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


class TestUnknownToolHandling:
    @pytest.mark.asyncio
    async def test_unknown_tool_skipped(self):
        skill = Skill(
            name="unknown",
            description="Unknown tool",
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
            steps=[SkillStep(tool="get_alert", args_template={"id": "{{alert_id}}"})],
        )
        handlers = {"get_alert": _mock_handler}

        result = await execute_skill(skill, {}, handlers)
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
        handlers = {"get_alert": _mock_handler}

        result = await execute_skill(skill, {"alert_id": "A1"}, handlers)
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
        """When skill.tools is populated, only those tools should be callable."""
        skill = Skill(
            name="restricted",
            description="Restricted",
            tools=["get_alert"],  # Only get_alert declared
            orchestration=True,
            steps=[
                SkillStep(tool="get_alert", args_template={"id": "A1"}),
            ],
        )
        # Provide many handlers but only get_alert is declared
        handlers = {
            "get_alert": _mock_handler,
            "delete_resources": _echo_handler,
            "get_resource": _mock_handler,
        }

        result = await execute_skill(skill, {}, handlers)
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_empty_tools_allows_all(self):
        """When skill.tools is empty, all available handlers are allowed."""
        skill = Skill(
            name="open",
            description="Open",
            tools=[],
            orchestration=True,
            steps=[
                SkillStep(tool="get_alert", args_template={"id": "A1"}),
                SkillStep(tool="get_resource", args_template={"id": "R1"}),
            ],
        )
        handlers = {"get_alert": _mock_handler, "get_resource": _mock_handler}

        result = await execute_skill(skill, {}, handlers)
        assert result["status"] == "completed"
        assert len(result["steps"]) == 2