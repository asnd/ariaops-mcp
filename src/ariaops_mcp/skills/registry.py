"""Skill registry — singleton with ContextVar override support for testability."""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar, Token
from pathlib import Path

from ariaops_mcp.skills.loader import load_skills_from_directory
from ariaops_mcp.skills.models import Skill

logger = logging.getLogger(__name__)

_TEMPLATE_RE = re.compile(r"\{\{([\w-]+)\}\}")


def render_template(body: str, arguments: dict[str, str] | None = None) -> str:
    """Substitute {{key}} placeholders in a body string.

    Substitution is single-pass only — values containing {{...}} patterns
    are NOT recursively expanded. This prevents template injection.
    """
    substitutions = arguments or {}

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in substitutions:
            return substitutions[key]
        return match.group(0)

    return _TEMPLATE_RE.sub(_replace, body)


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._directory: Path | None = None

    def load(self, directory: str | Path) -> None:
        self._directory = Path(directory)
        self._skills.clear()
        for skill in load_skills_from_directory(self._directory):
            self._skills[skill.name] = skill

    def reload(self) -> None:
        if self._directory is None:
            logger.warning("Cannot reload skills: no directory configured")
            return
        self.load(self._directory)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list(self) -> list[Skill]:
        return list(self._skills.values())

    def render_body(self, name: str, arguments: dict[str, str] | None = None) -> str:
        skill = self.get(name)
        if skill is None:
            raise ValueError(f"Skill not found: {name}")
        return render_template(skill.body, arguments)


# --- Singleton with ContextVar override (matching client.py pattern) ---

_registry: SkillRegistry | None = None
_registry_override: ContextVar[SkillRegistry | None] = ContextVar("ariaops_registry_override", default=None)


def get_registry() -> SkillRegistry:
    """Get the skill registry singleton (respects ContextVar overrides for testing)."""
    override = _registry_override.get()
    if override is not None:
        return override
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
    return _registry


def set_registry_override(registry: SkillRegistry) -> Token[SkillRegistry | None]:
    """Set a test override for the skill registry (returns token for reset)."""
    return _registry_override.set(registry)


def reset_registry_override(token: Token[SkillRegistry | None]) -> None:
    """Reset a previously set registry override."""
    _registry_override.reset(token)


def reset_registry() -> None:
    """Reset the global singleton (for tests only)."""
    global _registry
    _registry = None