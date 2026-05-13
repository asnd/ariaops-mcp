"""Tests for skills/loader.py."""

from ariaops_mcp.skills.loader import load_skills_from_directory, parse_skill_file


def _write_skill_file(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


class TestParseSkillFile:
    def test_valid_skill(self, tmp_path):
        content = """---
name: troubleshoot-alert
description: Investigate an alert
arguments:
  - name: alert_id
    description: Alert ID
    required: true
tools:
  - get_alert
orchestration: true
steps:
  - tool: get_alert
    args_template:
      alert_id: "{{alert_id}}"
    output_key: alert
---

# Troubleshoot Alert

Investigate alert {{alert_id}}.
"""
        path = _write_skill_file(tmp_path, "troubleshoot.md", content)
        skill = parse_skill_file(path)
        assert skill is not None
        assert skill.name == "troubleshoot-alert"
        assert skill.description == "Investigate an alert"
        assert len(skill.arguments) == 1
        assert skill.arguments[0].name == "alert_id"
        assert skill.arguments[0].required is True
        assert skill.tools == ["get_alert"]
        assert skill.orchestration is True
        assert len(skill.steps) == 1
        assert skill.steps[0].tool == "get_alert"
        assert skill.body.strip().startswith("# Troubleshoot Alert")

    def test_no_frontmatter(self, tmp_path):
        path = _write_skill_file(tmp_path, "no_frontmatter.md", "Just some text")
        skill = parse_skill_file(path)
        assert skill is None

    def test_invalid_yaml(self, tmp_path):
        content = "---\nname: [invalid\n---\nBody"
        path = _write_skill_file(tmp_path, "bad_yaml.md", content)
        skill = parse_skill_file(path)
        assert skill is None

    def test_minimal_skill(self, tmp_path):
        content = "---\nname: minimal\ndescription: Minimal skill\n---\n\nMinimal body"
        path = _write_skill_file(tmp_path, "minimal.md", content)
        skill = parse_skill_file(path)
        assert skill is not None
        assert skill.name == "minimal"
        assert skill.orchestration is False
        assert skill.arguments == []
        assert skill.steps == []

    def test_non_dict_frontmatter(self, tmp_path):
        content = "---\n- item1\n- item2\n---\nBody"
        path = _write_skill_file(tmp_path, "list_fm.md", content)
        skill = parse_skill_file(path)
        assert skill is None

    def test_windows_line_endings(self, tmp_path):
        """CRLF line endings should be handled correctly."""
        content = "---\r\nname: crlf-skill\r\ndescription: CRLF test\r\n---\r\n\r\nBody with CRLF"
        path = _write_skill_file(tmp_path, "crlf.md", content)
        skill = parse_skill_file(path)
        assert skill is not None
        assert skill.name == "crlf-skill"
        assert "Body with CRLF" in skill.body

    def test_invalid_skill_name_rejected(self, tmp_path):
        """Skill names with invalid characters should be rejected at parse time."""
        content = "---\nname: Invalid Name\ndescription: Bad\n---\n\nBody"
        path = _write_skill_file(tmp_path, "invalid_name.md", content)
        skill = parse_skill_file(path)
        assert skill is None  # model validation should fail


class TestLoadSkillsFromDirectory:
    def test_load_multiple_skills(self, tmp_path):
        skill1 = "---\nname: skill-one\ndescription: First skill\n---\n\nBody one"
        skill2 = "---\nname: skill-two\ndescription: Second skill\n---\n\nBody two"
        _write_skill_file(tmp_path, "skill1.md", skill1)
        _write_skill_file(tmp_path, "skill2.md", skill2)

        skills = load_skills_from_directory(tmp_path)
        assert len(skills) == 2
        names = {s.name for s in skills}
        assert names == {"skill-one", "skill-two"}

    def test_load_skips_invalid(self, tmp_path):
        valid = "---\nname: valid\ndescription: Valid skill\n---\n\nBody"
        invalid = "No frontmatter here"
        _write_skill_file(tmp_path, "valid.md", valid)
        _write_skill_file(tmp_path, "invalid.md", invalid)

        skills = load_skills_from_directory(tmp_path)
        assert len(skills) == 1
        assert skills[0].name == "valid"

    def test_load_nonexistent_directory(self, tmp_path):
        skills = load_skills_from_directory(tmp_path / "nonexistent")
        assert skills == []

    def test_load_empty_directory(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        skills = load_skills_from_directory(empty_dir)
        assert skills == []

    def test_duplicate_name_overwrites(self, tmp_path):
        skill_a = "---\nname: dup\ndescription: First\n---\n\nBody A"
        skill_b = "---\nname: dup\ndescription: Second\n---\n\nBody B"
        _write_skill_file(tmp_path, "a.md", skill_a)
        _write_skill_file(tmp_path, "b.md", skill_b)

        skills = load_skills_from_directory(tmp_path)
        assert len(skills) == 1
        assert skills[0].description == "Second"