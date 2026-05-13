"""Tests for skills/prompts.py."""

import mcp.types as types

from ariaops_mcp.skills.models import Skill, SkillArgument
from ariaops_mcp.skills.prompts import render_prompt, skill_to_prompt


class TestSkillToPrompt:
    def test_skill_without_arguments(self):
        skill = Skill(name="test-skill", description="A test skill")
        prompt = skill_to_prompt(skill)
        assert isinstance(prompt, types.Prompt)
        assert prompt.name == "test-skill"
        assert prompt.description == "A test skill"
        assert prompt.arguments == []

    def test_skill_with_arguments(self):
        skill = Skill(
            name="troubleshoot",
            description="Troubleshoot",
            arguments=[
                SkillArgument(name="alert_id", description="Alert ID", required=True),
                SkillArgument(name="depth", description="Depth", required=False),
            ],
        )
        prompt = skill_to_prompt(skill)
        assert len(prompt.arguments) == 2
        assert prompt.arguments[0].name == "alert_id"
        assert prompt.arguments[0].required is True
        assert prompt.arguments[1].name == "depth"
        assert prompt.arguments[1].required is False


class TestRenderPrompt:
    def test_render_without_args(self):
        skill = Skill(name="hello", description="Hello skill", body="\nHello World!\n")
        result = render_prompt(skill, None)
        assert isinstance(result, types.GetPromptResult)
        assert len(result.messages) == 1
        assert result.messages[0].role == "user"
        assert "Hello World!" in result.messages[0].content.text

    def test_render_with_args(self):
        skill = Skill(name="greet", description="Greeting", body="\nHello {{name}}!\n")
        result = render_prompt(skill, {"name": "Alice"})
        assert "Hello Alice!" in result.messages[0].content.text

    def test_render_no_registry_dependency(self):
        """render_prompt should work without any registry being initialized."""
        skill = Skill(name="standalone", description="Standalone", body="Content {{x}}")
        result = render_prompt(skill, {"x": "value"})
        assert "Content value" in result.messages[0].content.text

    def test_render_preserves_unresolved_placeholders(self):
        skill = Skill(name="partial", description="Partial", body="{{resolved}} and {{unresolved}}")
        result = render_prompt(skill, {"resolved": "YES"})
        assert "YES" in result.messages[0].content.text
        assert "{{unresolved}}" in result.messages[0].content.text