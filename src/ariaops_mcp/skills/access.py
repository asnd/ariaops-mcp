"""Per-principal access control for skills.

Skills may declare ``roles:``, ``countries:`` and/or ``instances:`` in their
frontmatter. A skill is visible/executable when *every* declared dimension
matches the request principal (dimensions are ANDed; values within one
dimension are ORed). Undeclared dimensions are unrestricted, so existing
skills keep working unchanged.
"""

from __future__ import annotations

from ariaops_mcp.config import Settings, get_settings
from ariaops_mcp.principal import Principal
from ariaops_mcp.skills.models import Skill
from ariaops_mcp.skills.registry import get_registry


def skill_allowed(skill: Skill, principal: Principal | None, settings: Settings | None = None) -> bool:
    """Whether *principal* may see and execute *skill*.

    ``principal is None`` means principal resolution failed (e.g. claims that
    map to no instance) — fail closed: only fully-unrestricted skills remain.
    """
    restricted = bool(skill.roles or skill.countries or skill.instances)
    if not restricted:
        return True
    if principal is None:
        return False

    if skill.roles and principal.role.lower() not in skill.roles:
        return False

    accessible_ids = {iid.lower() for iid in principal.instance_ids}
    if skill.instances and not accessible_ids & set(skill.instances):
        return False

    if skill.countries:
        settings = settings or get_settings()
        accessible_countries = {
            inst.country.lower()
            for inst in settings.resolved_instances()
            if inst.country and principal.can_access(inst.id)
        }
        if not accessible_countries & set(skill.countries):
            return False

    return True


def visible_skills(principal: Principal | None, settings: Settings | None = None) -> list[Skill]:
    """Skills from the registry that *principal* is allowed to use."""
    settings = settings or get_settings()
    return [s for s in get_registry().list() if skill_allowed(s, principal, settings)]
