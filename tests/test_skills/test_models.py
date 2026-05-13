"""Tests for skills/models.py."""

import pytest

from ariaops_mcp.skills.models import Skill, SkillArgument, SkillStep


class TestSkillArgument:
    def test_defaults(self):
        arg = SkillArgument(name="alert_id")
        assert arg.name == "alert_id"
        assert arg.description is None
        assert arg.required is False

    def test_full(self):
        arg = SkillArgument(name="depth", description="Investigation depth", required=False)
        assert arg.description == "Investigation depth"

    def test_valid_with_hyphens(self):
        """Argument names with hyphens should be valid."""
        arg = SkillArgument(name="time-range-hours")
        assert arg.name == "time-range-hours"

    def test_invalid_name_uppercase(self):
        with pytest.raises(Exception):
            SkillArgument(name="InvalidName")

    def test_invalid_name_spaces(self):
        with pytest.raises(Exception):
            SkillArgument(name="bad name")

    def test_invalid_name_start_with_hyphen(self):
        with pytest.raises(Exception):
            SkillArgument(name="-bad")

    def test_invalid_name_empty(self):
        with pytest.raises(Exception):
            SkillArgument(name="")


class TestSkillStep:
    def test_full(self):
        step = SkillStep(
            tool="get_alert",
            args_template={"alert_id": "{{alert_id}}"},
            output_key="alert",
        )
        assert step.tool == "get_alert"
        assert step.args_template["alert_id"] == "{{alert_id}}"
        assert step.output_key == "alert"

    def test_defaults(self):
        step = SkillStep(tool="get_resource", args_template={"id": "{{resource_id}}"})
        assert step.output_key is None


class TestSkillNameValidation:
    def test_valid_names(self):
        for name in ["troubleshoot-alert", "my_skill", "a1-b2_c3", "x"]:
            skill = Skill(name=name, description="ok")
            assert skill.name == name

    def test_invalid_names(self):
        for name in ["Uppercase", "with spaces", "has/slash", "has.dot", "-starts-with-hyphen", ""]:
            with pytest.raises(Exception):
                Skill(name=name, description="fail")

    def test_unicode_rejected(self):
        with pytest.raises(Exception):
            Skill(name="ñoño", description="fail")


class TestSkillStepsToolsValidation:
    def test_steps_tools_must_match_declared(self):
        """Steps referencing undeclared tools should fail validation."""
        with pytest.raises(Exception, match="not declared"):
            Skill(
                name="bad-skill",
                description="bad",
                tools=["get_alert"],
                steps=[SkillStep(tool="delete_resources", args_template={})],
            )

    def test_steps_ok_when_tools_empty(self):
        """When tools list is empty, no restriction on steps."""
        skill = Skill(
            name="open-skill",
            description="open",
            tools=[],
            steps=[SkillStep(tool="anything", args_template={})],
        )
        assert len(skill.steps) == 1

    def test_steps_ok_when_tools_match(self):
        """Steps that match declared tools should pass."""
        skill = Skill(
            name="good-skill",
            description="good",
            tools=["get_alert", "get_resource"],
            steps=[
                SkillStep(tool="get_alert", args_template={}),
                SkillStep(tool="get_resource", args_template={}),
            ],
        )
        assert len(skill.steps) == 2


class TestOrchestrationRequiresTools:
    def test_orchestration_without_tools_rejected(self):
        """Orchestration skills must declare at least one tool."""
        with pytest.raises(Exception, match="Orchestration-enabled skills must declare"):
            Skill(
                name="bad-orch",
                description="Orchestration without tools",
                orchestration=True,
                steps=[SkillStep(tool="get_alert", args_template={})],
            )

    def test_orchestration_with_tools_accepted(self):
        """Orchestration with non-empty tools should pass."""
        skill = Skill(
            name="good-orch",
            description="Orchestration with tools",
            tools=["get_alert"],
            orchestration=True,
            steps=[SkillStep(tool="get_alert", args_template={})],
        )
        assert skill.orchestration is True

    def test_non_orchestration_without_tools_accepted(self):
        """Non-orchestration skills don't need tools."""
        skill = Skill(
            name="info-skill",
            description="Just instructions",
            orchestration=False,
        )
        assert skill.tools == []


class TestSkillMinimalAndFull:
    def test_minimal(self):
        skill = Skill(name="test-skill", description="A test skill")
        assert skill.name == "test-skill"
        assert skill.arguments == []
        assert skill.tools == []
        assert skill.orchestration is False
        assert skill.steps == []
        assert skill.body == ""
        assert skill.source_path is None

    def test_full(self):
        skill = Skill(
            name="troubleshoot-alert",
            description="Investigate an alert",
            arguments=[SkillArgument(name="alert_id", description="Alert ID", required=True)],
            tools=["get_alert", "get_resource"],
            orchestration=True,
            steps=[SkillStep(tool="get_alert", args_template={"alert_id": "{{alert_id}}"}, output_key="alert")],
            body="# Troubleshoot Alert\n\nInvestigate {{alert_id}}",
            source_path="/path/to/skill.md",
        )
        assert len(skill.arguments) == 1
        assert skill.orchestration is True
        assert skill.body.startswith("# Troubleshoot")