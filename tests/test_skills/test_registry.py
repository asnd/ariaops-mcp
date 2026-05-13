"""Tests for skills/registry.py."""

import pytest

from ariaops_mcp.skills.registry import (
    SkillRegistry,
    get_registry,
    render_template,
    reset_registry,
    reset_registry_override,
    set_registry_override,
)


class TestRenderTemplate:
    def test_basic_substitution(self):
        result = render_template("Hello {{name}}!", {"name": "World"})
        assert result == "Hello World!"

    def test_missing_key_preserved(self):
        result = render_template("Hello {{name}}!", {})
        assert result == "Hello {{name}}!"

    def test_multiple_keys(self):
        result = render_template("{{a}} and {{b}}", {"a": "X", "b": "Y"})
        assert result == "X and Y"

    def test_no_recursive_expansion(self):
        result = render_template("Hello {{name}}!", {"name": "{{injected}}"})
        assert result == "Hello {{injected}}!"

    def test_empty_arguments(self):
        result = render_template("No placeholders here", None)
        assert result == "No placeholders here"

    def test_hyphens_in_keys(self):
        """Keys with hyphens should resolve correctly (#3)."""
        result = render_template("{{my-arg}} is set", {"my-arg": "yes"})
        assert result == "yes is set"


class TestSkillRegistry:
    def test_load_and_list(self, tmp_path):
        skill_content = "---\nname: test-skill\ndescription: Test\n---\n\nBody"
        (tmp_path / "skill.md").write_text(skill_content)

        reg = SkillRegistry()
        reg.load(tmp_path)
        skills = reg.list()
        assert len(skills) == 1
        assert skills[0].name == "test-skill"

    def test_get_existing(self, tmp_path):
        skill_content = "---\nname: my-skill\ndescription: My skill\n---\n\nBody"
        (tmp_path / "skill.md").write_text(skill_content)

        reg = SkillRegistry()
        reg.load(tmp_path)
        skill = reg.get("my-skill")
        assert skill is not None
        assert skill.name == "my-skill"

    def test_get_nonexistent(self, tmp_path):
        reg = SkillRegistry()
        reg.load(tmp_path)
        assert reg.get("nope") is None

    def test_reload(self, tmp_path):
        skill_content = "---\nname: first\ndescription: First\n---\n\nBody"
        (tmp_path / "skill.md").write_text(skill_content)

        reg = SkillRegistry()
        reg.load(tmp_path)
        assert len(reg.list()) == 1

        (tmp_path / "skill2.md").write_text("---\nname: second\ndescription: Second\n---\n\nBody2")
        reg.reload()
        assert len(reg.list()) == 2

    def test_reload_without_directory(self):
        reg = SkillRegistry()
        reg.reload()

    def test_render_body_with_substitution(self, tmp_path):
        content = "---\nname: greet\ndescription: Greeting\n---\n\nHello {{name}}!"
        (tmp_path / "skill.md").write_text(content)

        reg = SkillRegistry()
        reg.load(tmp_path)
        result = reg.render_body("greet", {"name": "World"})
        assert result.strip() == "Hello World!"

    def test_render_body_missing_key(self, tmp_path):
        content = "---\nname: greet\ndescription: Greeting\n---\n\nHello {{name}}!"
        (tmp_path / "skill.md").write_text(content)

        reg = SkillRegistry()
        reg.load(tmp_path)
        result = reg.render_body("greet", {})
        assert "{{name}}" in result

    def test_render_body_unknown_skill(self, tmp_path):
        reg = SkillRegistry()
        reg.load(tmp_path)
        with pytest.raises(ValueError, match="Skill not found"):
            reg.render_body("nonexistent")


class TestGetRegistry:
    def setup_method(self):
        reset_registry()

    def teardown_method(self):
        reset_registry()

    def test_singleton(self):
        a = get_registry()
        b = get_registry()
        assert a is b

    def test_reset(self):
        reg = get_registry()
        reset_registry()
        new_reg = get_registry()
        assert reg is not new_reg


class TestContextVarOverride:
    def setup_method(self):
        reset_registry()

    def teardown_method(self):
        reset_registry()

    def test_override_is_used(self, tmp_path):
        content = "---\nname: override-skill\ndescription: Override\n---\n\nOverride body"
        (tmp_path / "skill.md").write_text(content)

        override_reg = SkillRegistry()
        override_reg.load(tmp_path)

        token = set_registry_override(override_reg)
        try:
            reg = get_registry()
            assert reg is override_reg
            assert reg.get("override-skill") is not None
        finally:
            reset_registry_override(token)

    def test_override_reset_restores_global(self, tmp_path):
        content = "---\nname: override-skill\ndescription: Override\n---\n\nBody"
        (tmp_path / "skill.md").write_text(content)

        override_reg = SkillRegistry()
        override_reg.load(tmp_path)

        global_reg = get_registry()
        assert global_reg.get("override-skill") is None

        token = set_registry_override(override_reg)
        assert get_registry().get("override-skill") is not None
        reset_registry_override(token)

        assert get_registry() is global_reg
        assert get_registry().get("override-skill") is None