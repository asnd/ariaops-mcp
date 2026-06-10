"""Tests for skills/access.py (role/country/instance restrictions)."""

from __future__ import annotations

import pytest

from ariaops_mcp.config import Settings
from ariaops_mcp.principal import Principal
from ariaops_mcp.skills.access import skill_allowed, visible_skills
from ariaops_mcp.skills.models import Skill
from ariaops_mcp.skills.registry import get_registry


def _skill(**kwargs) -> Skill:
    return Skill(name="test-skill", description="d", **kwargs)


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "ARIAOPS_TRANSPORT": "stdio",
            "ARIAOPS_INSTANCES": (
                '[{"id":"se","host":"se.example.com","username":"u","password":"p","country":"SE"},'
                '{"id":"de","host":"de.example.com","username":"u","password":"p","country":"DE"}]'
            ),
        }
    )


_OPS = Principal(role="ops", instance_ids=("se", "de"), default_instance_id=None)
_SE = Principal(role="country", instance_ids=("se",), default_instance_id="se")


# ── skill_allowed ─────────────────────────────────────────────────────────────


def test_unrestricted_skill_allowed_for_everyone():
    skill = _skill()
    assert skill_allowed(skill, _OPS, _settings())
    assert skill_allowed(skill, _SE, _settings())
    assert skill_allowed(skill, None, _settings())


def test_role_restriction():
    skill = _skill(roles=["ops"])
    assert skill_allowed(skill, _OPS, _settings())
    assert not skill_allowed(skill, _SE, _settings())


def test_role_restriction_case_insensitive():
    skill = _skill(roles=["OPS "])
    assert skill_allowed(skill, _OPS, _settings())


def test_instance_restriction():
    skill = _skill(instances=["se"])
    assert skill_allowed(skill, _OPS, _settings())  # ops can access se
    assert skill_allowed(skill, _SE, _settings())
    de = Principal(role="country", instance_ids=("de",), default_instance_id="de")
    assert not skill_allowed(skill, de, _settings())


def test_country_restriction():
    skill = _skill(countries=["DE"])
    settings = _settings()
    assert skill_allowed(skill, _OPS, settings)  # ops reaches the DE instance
    assert not skill_allowed(skill, _SE, settings)


def test_dimensions_are_anded():
    skill = _skill(roles=["country"], countries=["SE"])
    settings = _settings()
    assert skill_allowed(skill, _SE, settings)
    assert not skill_allowed(skill, _OPS, settings)  # right country, wrong role


def test_restricted_skill_denied_for_none_principal():
    """Failed principal resolution fails closed for restricted skills."""
    assert not skill_allowed(_skill(roles=["ops"]), None, _settings())
    assert not skill_allowed(_skill(instances=["se"]), None, _settings())
    assert not skill_allowed(_skill(countries=["SE"]), None, _settings())


# ── visible_skills ────────────────────────────────────────────────────────────


@pytest.fixture()
def _registry_with_skills():
    registry = get_registry()
    saved = dict(registry._skills)
    registry._skills = {
        "open": Skill(name="open", description="d"),
        "ops-only": Skill(name="ops-only", description="d", roles=["ops"]),
        "se-only": Skill(name="se-only", description="d", countries=["SE"]),
    }
    try:
        yield registry
    finally:
        registry._skills = saved


def test_visible_skills_filters_by_principal(_registry_with_skills):
    settings = _settings()
    assert len(visible_skills(_OPS, settings)) == 3
    se_visible = visible_skills(_SE, settings)
    assert len(se_visible) == 2  # open + se-only
    none_visible = visible_skills(None, settings)
    assert len(none_visible) == 1  # only the unrestricted skill


# ── Skill model fields ────────────────────────────────────────────────────────


def test_skill_restriction_fields_default_empty():
    skill = _skill()
    assert skill.roles == [] and skill.countries == [] and skill.instances == []


def test_skill_restriction_fields_normalized():
    skill = _skill(roles=[" OPS ", ""], countries=["se"], instances=["DE"])
    assert skill.roles == ["ops"]
    assert skill.countries == ["se"]
    assert skill.instances == ["de"]
