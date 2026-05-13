"""SKILL.md file parser — extracts YAML frontmatter and markdown body."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from ariaops_mcp.skills.models import Skill

logger = logging.getLogger(__name__)

# Handles both Unix (\n) and Windows (\r\n) line endings.
_FRONTMATTER_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", re.DOTALL)

# Case-insensitive .md/.MD glob glob — returns unique paths.
_GLOB_PATTERNS = ("*.md", "*.MD")


def _iter_skill_paths(directory: Path):
    """Yield all .md/.MD file paths in the directory, deduplicated."""
    seen: set[str] = set()
    for pattern in _GLOB_PATTERNS:
        for path in sorted(directory.glob(pattern)):
            key = str(path.resolve())
            if key not in seen:
                seen.add(key)
                yield path


def parse_skill_file(path: Path) -> Skill | None:
    """Parse a single SKILL.md file into a Skill model, or None on failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        logger.warning("Skill file %s is not valid UTF-8; skipping", path)
        return None

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

    if "body" in raw:
        logger.warning(
            "Skill file %s has a 'body' key in frontmatter; it will be overwritten by the markdown body",
            path,
        )

    raw["body"] = body
    raw["source_path"] = str(path)

    try:
        skill = Skill.model_validate(raw)
    except ValidationError:
        logger.exception("Skill file %s failed validation; skipping", path)
        return None

    return skill


def load_skills_from_directory(directory: Path) -> list[Skill]:
    """Load all *.md skill files from a directory (non-recursive)."""
    if not directory.is_dir():
        logger.error("Skills directory does not exist: %s", directory)
        return []

    skills: dict[str, Skill] = {}
    errors: list[str] = []

    for path in _iter_skill_paths(directory):
        skill = parse_skill_file(path)
        if skill is None:
            continue

        if skill.name in skills:
            existing = skills[skill.name]
            msg = (
                f"Duplicate skill name '{skill.name}' in {path} (already defined in {existing.source_path}). "
                f"Skill names must be unique."
            )
            errors.append(msg)
            logger.error(msg)
            continue

        skills[skill.name] = skill

    if errors:
        logger.error("Encountered %d duplicate skill name(s); these skills were not loaded", len(errors))

    result = list(skills.values())
    logger.info("Loaded %d skill(s) from %s", len(result), directory)
    return result