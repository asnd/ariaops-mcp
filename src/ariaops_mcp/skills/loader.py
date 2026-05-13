"""SKILL.md file parser — extracts YAML frontmatter and markdown body."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from ariaops_mcp.skills.models import Skill

logger = logging.getLogger(__name__)

# Handles both Unix (\n) and Windows (\r\n) line endings.
_FRONTMATTER_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", re.DOTALL)


def parse_skill_file(path: Path) -> Skill | None:
    """Parse a single SKILL.md file into a Skill model, or None on failure."""
    text = path.read_text(encoding="utf-8")
    # Normalize CRLF to LF for consistent regex matching and body handling.
    text = text.replace("\r\n", "\n")

    match = _FRONTMATTER_RE.match(text)
    if not match:
        logger.warning("Skill file %s has no valid YAML frontmatter; skipping", path)
        return None

    frontmatter_str = match.group(1)
    body = text[match.end() :]

    try:
        raw = yaml.safe_load(frontmatter_str)
    except yaml.YAMLError:
        logger.exception("Skill file %s has invalid YAML frontmatter; skipping", path)
        return None

    if not isinstance(raw, dict):
        logger.warning("Skill file %s frontmatter is not a mapping; skipping", path)
        return None

    raw["body"] = body
    raw["source_path"] = str(path)

    try:
        skill = Skill.model_validate(raw)
    except Exception:
        logger.exception("Skill file %s failed validation; skipping", path)
        return None

    return skill


def load_skills_from_directory(directory: Path) -> list[Skill]:
    """Load all *.md skill files from a directory (non-recursive)."""
    if not directory.is_dir():
        logger.error("Skills directory does not exist: %s", directory)
        return []

    skills: dict[str, Skill] = {}

    for path in sorted(directory.glob("*.md")):
        skill = parse_skill_file(path)
        if skill is None:
            continue

        if skill.name in skills:
            logger.warning("Duplicate skill name '%s' from %s; overwriting previous", skill.name, path)

        skills[skill.name] = skill

    result = list(skills.values())
    logger.info("Loaded %d skill(s) from %s", len(result), directory)
    return result