"""Step-based orchestrator for skills with orchestration support.

Security: The executor restricts tool execution to only those tools declared
in the skill's 'tools' list. Write operations require explicit enablement
via ARIAOPS_ENABLE_WRITE_OPERATIONS. Template substitution is single-pass
to prevent injection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from ariaops_mcp.skills.models import Skill

logger = logging.getLogger(__name__)

# Match {{arg_name}} — supports hyphens in names.
_ARG_TEMPLATE_RE = re.compile(r"\{\{([\w-]+)\}\}")

# Match {{steps.N.field}} or {{steps.N.field.subfield}} — dot-delimited path traversal.
_STEP_REF_RE = re.compile(r"\{\{steps\.(\d+)\.([\w.]+)\}\}")

# Default per-step timeout in seconds.
_DEFAULT_STEP_TIMEOUT = 60.0


def _nested_get(data: dict[str, Any], dotted_path: str, default: Any = "") -> Any:
    """Traverse a dict by dotted path, e.g. 'alert.id' -> data['alert']['id']."""
    keys = dotted_path.split(".")
    current: Any = data
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default
    return current


def _resolve_value(
    template: str,
    arguments: dict[str, str],
    step_outputs: list[dict[str, Any] | None],
) -> str | None:
    """Resolve a single template value. Returns None if a required step ref is unavailable."""
    has_unresolved_ref = False

    def _replace_step_ref(match: re.Match[str]) -> str:
        nonlocal has_unresolved_ref
        step_idx = int(match.group(1))
        dotted_path = match.group(2)
        if step_idx < len(step_outputs):
            output = step_outputs[step_idx]
            if output is None:
                has_unresolved_ref = True
                return match.group(0)
            val = _nested_get(output, dotted_path, "")
            if isinstance(val, (dict, list)):
                return json.dumps(val)
            return str(val)
        logger.warning("Step reference %s could not be resolved (index out of range)", match.group(0))
        has_unresolved_ref = True
        return match.group(0)

    value = _STEP_REF_RE.sub(_replace_step_ref, template)
    if has_unresolved_ref:
        return None

    # Single-pass argument substitution (no recursive expansion).
    def _replace_arg(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in arguments:
            return arguments[key]
        return match.group(0)

    return _ARG_TEMPLATE_RE.sub(_replace_arg, value)


def _resolve_args(
    args_template: dict[str, str],
    arguments: dict[str, str],
    step_outputs: list[dict[str, Any] | None],
) -> dict[str, Any] | None:
    """Resolve all args for a step. Returns None if any step reference is unavailable."""
    resolved: dict[str, Any] = {}
    for key, template in args_template.items():
        value = _resolve_value(template, arguments, step_outputs)
        if value is None:
            return None
        # Only JSON-parse if the value looks like a structured literal.
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                resolved[key] = json.loads(value)
                continue
            except (json.JSONDecodeError, TypeError):
                pass
        resolved[key] = value
    return resolved


def _validate_required_arguments(skill: Skill, arguments: dict[str, str]) -> str | None:
    """Return an error message if required arguments are missing, else None."""
    missing = [arg.name for arg in skill.arguments if arg.required and arg.name not in arguments]
    if missing:
        return f"Missing required argument(s): {', '.join(missing)}"
    return None


def _build_allowed_handlers(
    skill: Skill,
    tool_handlers: dict[str, Callable[..., Awaitable[str]]],
    write_enabled: bool,
    write_tool_names: set[str],
) -> dict[str, Callable[..., Awaitable[str]]]:
    """Build a filtered dict of handlers allowed for this skill's execution.

    Restrictions applied:
    1. Only tools declared in skill.tools are allowed.
    2. Write tools are only included if write_enabled is True.
    """
    allowed: dict[str, Callable[..., Awaitable[str]]] = {}
    declared_tools = set(skill.tools) if skill.tools else set(tool_handlers.keys())

    for tool_name in declared_tools:
        if tool_name in write_tool_names and not write_enabled:
            logger.debug(
                "Skill '%s': blocking write tool '%s' (write operations disabled)",
                skill.name,
                tool_name,
            )
            continue
        handler = tool_handlers.get(tool_name)
        if handler is not None:
            allowed[tool_name] = handler

    return allowed


async def execute_skill(
    skill: Skill,
    arguments: dict[str, str],
    tool_handlers: dict[str, Callable[..., Awaitable[str]]],
    *,
    write_enabled: bool = False,
    write_tool_names: set[str] | None = None,
    step_timeout: float = _DEFAULT_STEP_TIMEOUT,
) -> dict[str, Any]:
    """Execute a skill's orchestration steps with output chaining.

    Args:
        skill: The skill to execute.
        arguments: User-provided arguments for template substitution.
        tool_handlers: All available tool handlers.
        write_enabled: Whether write operations are permitted.
        write_tool_names: Set of tool names considered write/mutating operations.
        step_timeout: Per-step timeout in seconds.

    Returns:
        Dict with execution results including per-step status and outputs.
    """
    if not skill.orchestration or not skill.steps:
        return {
            "skill": skill.name,
            "status": "error",
            "error": "Skill does not support orchestration or has no steps defined",
            "steps": [],
        }

    # Validate required arguments.
    arg_error = _validate_required_arguments(skill, arguments)
    if arg_error:
        return {
            "skill": skill.name,
            "status": "error",
            "error": arg_error,
            "steps": [],
        }

    # Build restricted handler set for this skill.
    allowed_handlers = _build_allowed_handlers(
        skill, tool_handlers, write_enabled, write_tool_names or set()
    )

    step_outputs: list[dict[str, Any] | None] = []
    results: list[dict[str, Any]] = []
    success_count = 0
    skip_count = 0

    for i, step in enumerate(skill.steps):
        handler = allowed_handlers.get(step.tool)
        if handler is None:
            reason = "Tool not allowed or unknown"
            if step.tool not in (write_tool_names or set()):
                reason = f"Unknown tool: {step.tool}"
            elif not write_enabled:
                reason = f"Write tool '{step.tool}' blocked (write operations disabled)"
            logger.warning("Skill '%s' step %d: %s; skipping", skill.name, i, reason)
            results.append({
                "step": i + 1,
                "tool": step.tool,
                "status": "skipped",
                "error": reason,
            })
            step_outputs.append(None)
            skip_count += 1
            continue

        # Resolve arguments — skip if a dependency on a failed/skipped step is detected.
        resolved_args = _resolve_args(step.args_template, arguments, step_outputs)
        if resolved_args is None:
            reason = "Skipped due to unresolved dependency on a failed/skipped upstream step"
            logger.warning("Skill '%s' step %d (%s): %s", skill.name, i, step.tool, reason)
            results.append({
                "step": i + 1,
                "tool": step.tool,
                "status": "skipped",
                "error": reason,
            })
            step_outputs.append(None)
            skip_count += 1
            continue

        try:
            raw_result = await asyncio.wait_for(handler(resolved_args), timeout=step_timeout)

            parsed: Any = raw_result
            if isinstance(raw_result, str):
                try:
                    parsed = json.loads(raw_result)
                except (json.JSONDecodeError, TypeError):
                    pass

            output: dict[str, Any] = {"result": parsed}
            if step.output_key:
                output[step.output_key] = parsed if isinstance(parsed, (dict, list)) else {"value": parsed}

            step_outputs.append(output)

            results.append({
                "step": i + 1,
                "tool": step.tool,
                "status": "success",
                "output": parsed,
            })
            success_count += 1

        except TimeoutError:
            logger.error("Skill '%s' step %d (%s) timed out after %.1fs", skill.name, i, step.tool, step_timeout)
            step_outputs.append(None)
            results.append({
                "step": i + 1,
                "tool": step.tool,
                "status": "error",
                "error": f"Step timed out after {step_timeout}s",
            })
        except Exception as exc:
            logger.exception("Skill '%s' step %d (%s) failed", skill.name, i, step.tool)
            step_outputs.append(None)
            results.append({
                "step": i + 1,
                "tool": step.tool,
                "status": "error",
                "error": str(exc),
            })

    total = len(skill.steps)
    status = "completed" if success_count == total else "partial" if success_count > 0 else "failed"

    return {
        "skill": skill.name,
        "status": status,
        "steps": results,
        "summary": f"Executed {success_count}/{total} steps successfully"
        + (f", {skip_count} skipped" if skip_count else ""),
    }