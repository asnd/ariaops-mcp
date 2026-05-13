"""Pydantic models for skill definitions."""

from __future__ import annotations

import re

from pydantic import BaseModel, field_validator

# Strict pattern for skill names: lowercase alphanumeric, hyphens, underscores only.
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class SkillArgument(BaseModel):
    name: str
    description: str | None = None
    required: bool = False


class SkillStep(BaseModel):
    tool: str
    args_template: dict[str, str]
    output_key: str | None = None


class Skill(BaseModel):
    name: str
    description: str
    arguments: list[SkillArgument] = []
    tools: list[str] = []
    orchestration: bool = False
    steps: list[SkillStep] = []
    body: str = ""
    source_path: str = ""

    @field_validator("name")
    @classmethod
    def name_must_be_uri_safe(cls, value: str) -> str:
        if not _SKILL_NAME_RE.match(value):
            raise ValueError(
                f"Skill name must match [a-z0-9][a-z0-9_-]* (got: {value!r}). "
                "Only lowercase letters, digits, hyphens, and underscores are allowed."
            )
        return value

    @field_validator("steps")
    @classmethod
    def steps_tools_must_be_declared(cls, steps: list[SkillStep], info) -> list[SkillStep]:  # noqa: N805
        """Validate that all step tools appear in the declared 'tools' list (if populated)."""
        tools_list = info.data.get("tools", [])
        if not tools_list or not steps:
            return steps
        for step in steps:
            if step.tool not in tools_list:
                raise ValueError(
                    f"Step references tool '{step.tool}' which is not declared in "
                    f"the skill's 'tools' list: {tools_list}"
                )
        return steps