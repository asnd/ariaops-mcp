"""MCP server setup and tool registration."""

import copy
import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import mcp.types as types
from mcp.server import Server
from pydantic import AnyUrl

from ariaops_mcp.circuit_breaker import CircuitOpenError
from ariaops_mcp.client import reset_current_instance, set_current_instance
from ariaops_mcp.config import get_settings
from ariaops_mcp.logging_config import new_correlation_id
from ariaops_mcp.principal import AccessDenied, Principal, resolve_principal
from ariaops_mcp.skills.executor import execute_skill as _run_skill_orchestration
from ariaops_mcp.skills.prompts import render_prompt, skill_to_prompt
from ariaops_mcp.skills.registry import get_registry
from ariaops_mcp.tools import alerts, ansible_inventory, capacity, discovery, metrics, reports, resources, write_ops

logger = logging.getLogger(__name__)

READ_ONLY_MODULES = [resources, alerts, metrics, capacity, reports, discovery]
WRITE_MODULES = [write_ops, ansible_inventory]

# Write-operation tool names (always known, independent of whether they're enabled).
_WRITE_TOOL_NAMES: set[str] = {t.name for mod in WRITE_MODULES for t in mod.tool_definitions()}

# Meta-tools that operate on the server itself and are not bound to a single
# Aria Operations instance.
_INSTANCE_AGNOSTIC_TOOLS: set[str] = {"list_skills", "reload_skills", "list_instances"}

_INSTANCE_ARG_SCHEMA: dict[str, Any] = {
    "type": "string",
    "description": (
        "Target Aria Operations instance id. Required for the 'ops' role when "
        "multiple instances are configured; for the 'country' role the instance "
        "is fixed and this argument is ignored. Use 'list_instances' to discover "
        "accessible instances."
    ),
}


def _with_instance_arg(tool: types.Tool) -> types.Tool:
    """Return a copy of ``tool`` whose input schema advertises the ``instance`` arg."""
    if not isinstance(tool.inputSchema, dict):
        return tool
    schema = copy.deepcopy(tool.inputSchema)
    properties = schema.setdefault("properties", {})
    if isinstance(properties, dict):
        properties.setdefault("instance", _INSTANCE_ARG_SCHEMA)
    return tool.model_copy(update={"inputSchema": schema})


def _write_operations_enabled() -> bool:
    try:
        return get_settings().enable_write_operations
    except Exception:
        logger.debug("Settings unavailable while checking write operations; defaulting to disabled.")
        return False


# --- Lazy tool registry (avoids import-time coupling to env vars) ---

_tool_defs: list[types.Tool] | None = None
_tool_handlers: dict[str, Callable[..., Awaitable[str]]] | None = None
# Legacy compatibility hooks used by the in-repo test UI.
_TOOL_DEFS: list[types.Tool] | None = None
_TOOL_HANDLERS: dict[str, Callable[..., Awaitable[str]]] | None = None


def _get_tool_registry() -> tuple[list[types.Tool], dict[str, Callable[..., Awaitable[str]]]]:
    """Build and cache the tool registry on first access (not at import time)."""
    global _tool_defs, _tool_handlers
    if _tool_defs is None or _tool_handlers is None:
        defs: list[types.Tool] = []
        handlers: dict[str, Callable[..., Awaitable[str]]] = {}
        for mod in READ_ONLY_MODULES:
            defs.extend(mod.tool_definitions())
            handlers.update(mod.tool_handlers())
        if _write_operations_enabled():
            for mod in WRITE_MODULES:
                defs.extend(mod.tool_definitions())
                handlers.update(mod.tool_handlers())
        _tool_defs = defs
        _tool_handlers = handlers
    return _tool_defs, _tool_handlers


def _build_registry() -> tuple[list[types.Tool], dict[str, Callable[..., Awaitable[str]]]]:
    """Backward-compatible wrapper for the test UI registry bootstrap."""
    if _TOOL_DEFS is not None and _TOOL_HANDLERS is not None:
        return _TOOL_DEFS, _TOOL_HANDLERS
    return _get_tool_registry()


def _init_skills() -> None:
    """Load skills from configured directory. Fails gracefully — logs and continues."""
    try:
        settings = get_settings()
        if settings.skills_dir:
            directory = Path(settings.skills_dir)
            if not directory.is_dir():
                logger.warning("ARIAOPS_SKILLS_DIR=%s does not exist or is not a directory; skills disabled", directory)
                return
            registry = get_registry()
            registry.load(directory)
            logger.info("Skills loaded from %s (%d skills)", settings.skills_dir, len(registry.list()))
    except Exception:
        logger.exception("Failed to initialize skills; server will start without skill support")


# --- Skill meta-tool definitions (always exposed when ARIAOPS_SKILLS_DIR is configured) ---


def _skill_tool_defs() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_skills",
            description="List available agent skills with metadata",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        types.Tool(
            name="execute_skill",
            description="Execute a skill's orchestration steps server-side. "
            "The skill must have orchestration enabled and define steps.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the skill to execute",
                    },
                    "arguments": {
                        "type": "object",
                        "description": "Arguments to pass to the skill's step templates",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["name"],
            },
        ),
        types.Tool(
            name="reload_skills",
            description="Re-scan the skills directory and reload all skill definitions",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
    ]


def _format_skill_error(error: str, cid: str, **extra: Any) -> str:
    """Format skill handler errors consistently with the rest of the codebase."""
    payload: dict[str, Any] = {"error": error, "correlation_id": cid}
    payload.update(extra)
    return json.dumps(payload)


async def _handle_list_skills(_args: dict[str, Any], _cid: str) -> str:
    registry = get_registry()
    skills = registry.list()
    result = [
        {
            "name": s.name,
            "description": s.description,
            "arguments": [a.model_dump() for a in s.arguments],
            "tools": s.tools,
            "orchestration": s.orchestration,
        }
        for s in skills
    ]
    return json.dumps(result, indent=2)


async def _handle_execute_skill(args: dict[str, Any], cid: str) -> str:
    name = args.get("name", "")
    raw_arguments = args.get("arguments")
    if raw_arguments is not None and not isinstance(raw_arguments, dict):
        return _format_skill_error(
            f"'arguments' must be an object, got {type(raw_arguments).__name__}", cid
        )
    arguments: dict[str, Any] = raw_arguments or {}
    registry = get_registry()
    skill = registry.get(name)

    if skill is None:
        return _format_skill_error(
            f"Skill not found: {name}", cid, available=[s.name for s in registry.list()]
        )
    if not skill.orchestration:
        return _format_skill_error(f"Skill '{name}' does not support orchestration", cid, orchestration=False)

    _, tool_handlers = _get_tool_registry()

    result = await _run_skill_orchestration(
        skill,
        arguments,
        tool_handlers,
        write_enabled=_write_operations_enabled(),
        write_tool_names=_WRITE_TOOL_NAMES,
    )
    result["correlation_id"] = cid
    return json.dumps(result, indent=2, default=str)


async def _handle_reload_skills(_args: dict[str, Any], cid: str) -> str:
    registry = get_registry()
    try:
        registry.reload()
        count = len(registry.list())
        return json.dumps({
            "status": "ok",
            "skills_loaded": count,
            "skill_names": [s.name for s in registry.list()],
            "correlation_id": cid,
        })
    except Exception as e:
        logger.exception("Failed to reload skills")
        return _format_skill_error(str(e), cid)


_SKILL_TOOL_HANDLERS: dict[str, Callable[..., Awaitable[str]]] = {
    "list_skills": _handle_list_skills,
    "execute_skill": _handle_execute_skill,
    "reload_skills": _handle_reload_skills,
}


def _instance_tool_defs() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_instances",
            description="List the Aria Operations instances accessible to the current user/role.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]


def _current_claims() -> dict[str, Any] | None:
    """Return the validated JWT claims for the current request, if any."""
    try:
        from mcp.server.auth.middleware.auth_context import get_access_token

        token = get_access_token()
    except Exception:
        return None
    if token is None:
        return None
    # OAuth tokens (JWTTokenVerifier) carry claims; LDAP tokens attach them via
    # the ClaimsAccessToken subclass. Use getattr so a token type without a
    # claims field falls back to role/default resolution rather than erroring.
    return getattr(token, "claims", None)


def _resolve_principal_for_request() -> Principal:
    return resolve_principal(claims=_current_claims())


async def _handle_list_instances(_args: dict[str, Any], cid: str) -> str:
    try:
        principal = _resolve_principal_for_request()
    except AccessDenied as e:
        return json.dumps({"error": "Access denied", "detail": str(e), "correlation_id": cid})
    settings = get_settings()
    accessible = [
        {"id": inst.id, "host": inst.host, "country": inst.country}
        for inst in settings.resolved_instances()
        if principal.can_access(inst.id)
    ]
    return json.dumps(
        {
            "role": principal.role,
            "default_instance": principal.default_instance_id,
            "instances": accessible,
            "correlation_id": cid,
        },
        indent=2,
    )


_META_TOOL_HANDLERS: dict[str, Callable[..., Awaitable[str]]] = {
    **_SKILL_TOOL_HANDLERS,
    "list_instances": _handle_list_instances,
}


def _skills_configured() -> bool:
    """Check if skills directory is configured (regardless of whether skills loaded)."""
    try:
        return bool(get_settings().skills_dir)
    except Exception:
        return False


def create_server() -> Server:
    """Create and configure the MCP server with all handlers."""
    _init_skills()

    server = Server("ariaops-mcp")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        tool_defs, _ = _get_tool_registry()
        # Advertise the optional per-request `instance` argument on every
        # instance-bound tool so MCP clients can target a specific instance.
        tools = [_with_instance_arg(t) for t in tool_defs]
        # `list_instances` is always available for instance discovery.
        tools.extend(_instance_tool_defs())
        # Skill meta-tools are exposed only when a skills directory is configured.
        if _skills_configured():
            skill_defs = _skill_tool_defs()
            tools.extend(
                _with_instance_arg(t) if t.name == "execute_skill" else t for t in skill_defs
            )
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
        cid = new_correlation_id()
        start = time.monotonic()
        args: dict[str, Any] = dict(arguments or {})

        async def _run(coro: Awaitable[str]) -> str:
            try:
                return await coro
            except CircuitOpenError as e:
                return json.dumps({
                    "error": "Service unavailable",
                    "detail": str(e),
                    "retry_after": e.retry_after,
                    "correlation_id": cid,
                })
            except TimeoutError:
                return json.dumps({
                    "error": "Request deadline exceeded",
                    "detail": f"Total time exceeded {get_settings().request_deadline}s including retries",
                    "correlation_id": cid,
                })
            except Exception as e:
                logger.exception("Tool '%s' failed unexpectedly", name)
                return json.dumps({"error": "Unexpected error", "detail": str(e), "correlation_id": cid})

        def _log_done() -> None:
            duration_ms = (time.monotonic() - start) * 1000
            logger.info(
                "tool_call: %s [%s] %.0fms",
                name,
                cid,
                duration_ms,
                extra={"event": "tool_call", "tool": name, "duration_ms": duration_ms},
            )

        # Resolve which handler serves this tool.
        is_skill_tool = name in _SKILL_TOOL_HANDLERS
        if is_skill_tool and not _skills_configured():
            raise ValueError(f"Unknown tool: {name}")

        meta_handler = _META_TOOL_HANDLERS.get(name)
        if meta_handler is not None and name in _INSTANCE_AGNOSTIC_TOOLS:
            # Server meta-tools that are not bound to a specific instance.
            result = await _run(meta_handler(args, cid))
            _log_done()
            return [types.TextContent(type="text", text=result)]

        # Everything below is instance-bound: resolve the caller's principal,
        # enforce access to the requested instance, and pin the client context.
        requested_instance = args.pop("instance", None)
        if requested_instance is not None:
            requested_instance = str(requested_instance)
        try:
            principal = _resolve_principal_for_request()
            instance_id = principal.resolve_instance(requested_instance)
        except AccessDenied as e:
            result = json.dumps({"error": "Access denied", "detail": str(e), "correlation_id": cid})
            _log_done()
            return [types.TextContent(type="text", text=result)]

        if meta_handler is not None:
            handler_coro = meta_handler(args, cid)
        else:
            _, tool_handlers = _get_tool_registry()
            std_handler = tool_handlers.get(name)
            if not std_handler:
                raise ValueError(f"Unknown tool: {name}")
            handler_coro = std_handler(args)

        instance_token = set_current_instance(instance_id)
        try:
            result = await _run(handler_coro)
        finally:
            reset_current_instance(instance_token)

        _log_done()
        return [types.TextContent(type="text", text=result)]

    @server.list_resources()
    async def list_resources() -> list[types.Resource]:
        resource_list = [
            types.Resource(
                uri=cast(AnyUrl, "ariaops://version"),
                name="Aria Operations Version",
                description="Current Aria Operations version and deployment info",
                mimeType="application/json",
            ),
            types.Resource(
                uri=cast(AnyUrl, "ariaops://adapter-kinds"),
                name="Aria Operations Adapter Kinds",
                description="All adapter kinds registered in Aria Operations",
                mimeType="application/json",
            ),
        ]

        registry = get_registry()
        for skill in registry.list():
            resource_list.append(
                types.Resource(
                    uri=cast(AnyUrl, f"ariaops://skills/{skill.name}"),
                    name=f"Skill: {skill.name}",
                    description=skill.description,
                    mimeType="text/markdown",
                )
            )

        return resource_list

    @server.read_resource()
    async def read_resource(uri: AnyUrl) -> str:
        from ariaops_mcp.client import get_client

        uri_str = str(uri)

        if uri_str == "ariaops://version":
            try:
                data = await get_client().get("/versions/current")
                return json.dumps(data, indent=2)
            except Exception as e:
                return json.dumps({"error": str(e)})
        elif uri_str == "ariaops://adapter-kinds":
            try:
                data = await get_client().get("/adapterkinds")
                return json.dumps(data, indent=2)
            except Exception as e:
                return json.dumps({"error": str(e)})
        elif uri_str.startswith("ariaops://skills/"):
            skill_name = uri_str[len("ariaops://skills/"):]
            registry = get_registry()
            skill = registry.get(skill_name)
            if skill is None:
                raise ValueError(f"Skill not found: {skill_name}")
            return skill.body
        else:
            raise ValueError(f"Unknown resource URI: {uri_str}")

    @server.list_prompts()
    async def handle_list_prompts() -> list[types.Prompt]:
        registry = get_registry()
        return [skill_to_prompt(s) for s in registry.list()]

    @server.get_prompt()
    async def handle_get_prompt(name: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
        registry = get_registry()
        skill = registry.get(name)
        if skill is None:
            raise ValueError(f"Skill not found: {name}")
        return render_prompt(skill, arguments)

    return server
