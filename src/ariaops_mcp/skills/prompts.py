"""MCP Prompt conversion utilities for skills."""

from __future__ import annotations

import mcp.types as types

from ariaops_mcp.skills.models import Skill
from ariaops_mcp.skills.registry import render_template


def skill_to_prompt(skill: Skill) -> types.Prompt:
    """Convert a Skill model to an MCP Prompt definition."""
    return types.Prompt(
        name=skill.name,
        description=skill.description,
        arguments=[
            types.PromptArgument(
                name=arg.name,
                description=arg.description,
                required=arg.required,
            )
            for arg in skill.arguments
        ],
    )


def render_prompt(skill: Skill, arguments: dict[str, str] | None) -> types.GetPromptResult:
    """Render a skill into a GetPromptResult with template substitution.

    Uses the standalone render_template function directly on skill.body,
    avoiding any registry dependency.
    """
    body = render_template(skill.body, arguments)
    return types.GetPromptResult(
        description=skill.description,
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text=body),
            ),
        ],
    )