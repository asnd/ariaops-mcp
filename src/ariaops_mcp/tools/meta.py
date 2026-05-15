"""Meta-tools for multi-instance management.

Provides tools for listing, selecting, and querying across vROps instances.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import mcp.types as types

from ariaops_mcp.circuit_breaker import CircuitOpenError, CircuitState
from ariaops_mcp.client import get_client_pool
from ariaops_mcp.instances import get_instance_registry
from ariaops_mcp.resolver import (
    get_session_instance,
    set_session_instance,
)

logger = logging.getLogger(__name__)


# ── Tool Definitions ──────────────────────────────────────────────────────────


def tool_definitions() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_instances",
            description=(
                "List available vROps instances with their environment type, "
                "description, write-enabled status, and health."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "environment": {
                        "type": "string",
                        "enum": ["production", "preproduction", "testbed"],
                        "description": "Filter by environment type",
                    },
                },
            },
        ),
        types.Tool(
            name="select_instance",
            description=(
                "Set the session-level default vROps instance. "
                "Subsequent tool calls will use this instance unless overridden."
            ),
            inputSchema={
                "type": "object",
                "required": ["instance"],
                "properties": {
                    "instance": {
                        "type": "string",
                        "description": "Instance name to set as session default",
                    },
                },
            },
        ),
        types.Tool(
            name="instance_health",
            description=(
                "Get health status for one or all vROps instances. "
                "Shows circuit breaker state, token validity, and last request time."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "instance": {
                        "type": "string",
                        "description": "Instance name (omit for all instances)",
                    },
                },
            },
        ),
        types.Tool(
            name="query_all_instances",
            description=(
                "Broadcast a read-only tool call to multiple vROps instances "
                "and aggregate results. Use for cross-instance queries like "
                "'show critical alerts across all production instances'."
            ),
            inputSchema={
                "type": "object",
                "required": ["tool_name"],
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "description": "Name of the read-only tool to execute on each instance",
                    },
                    "tool_args": {
                        "type": "object",
                        "description": "Arguments to pass to the tool (same for each instance)",
                        "default": {},
                    },
                    "filter_environment": {
                        "type": "string",
                        "enum": ["production", "preproduction", "testbed"],
                        "description": "Only query instances of this environment type",
                    },
                    "filter_labels": {
                        "type": "object",
                        "description": (
                            "Only query instances matching these labels "
                            "(e.g. {\"countries\": [\"SE\", \"FI\"]})"
                        ),
                    },
                },
            },
        ),
    ]


# ── Tool Handlers ─────────────────────────────────────────────────────────────


async def _handle_list_instances(args: dict[str, Any]) -> str:
    registry = get_instance_registry()
    pool = get_client_pool()
    environment = args.get("environment")

    instances = registry.list_instances(environment=environment)
    session_default = get_session_instance()
    server_default = registry.get_default_instance_name()

    result = []
    for inst in instances:
        health = pool.get_health(inst.name)
        entry = {
            "name": inst.name,
            "host": inst.host,
            "environment": inst.environment,
            "description": inst.description,
            "write_enabled": inst.write_enabled,
            "labels": inst.labels,
            "health": health,
            "is_session_default": inst.name == session_default,
            "is_server_default": inst.name == server_default,
        }
        result.append(entry)

    return json.dumps({
        "instances": result,
        "total": len(result),
        "session_default": session_default,
        "server_default": server_default,
    }, indent=2)


async def _handle_select_instance(args: dict[str, Any]) -> str:
    instance_name = args.get("instance", "")
    if not instance_name:
        return json.dumps({"error": "Missing required argument: instance"})

    registry = get_instance_registry()
    config = registry.get(instance_name)
    if config is None:
        return json.dumps({
            "error": f"Instance '{instance_name}' not found.",
            "available": registry.instance_names(),
        })

    set_session_instance(instance_name)
    return json.dumps({
        "status": "ok",
        "message": f"Session default set to '{instance_name}'",
        "instance": {
            "name": config.name,
            "host": config.host,
            "environment": config.environment,
            "write_enabled": config.write_enabled,
        },
    })


async def _handle_instance_health(args: dict[str, Any]) -> str:
    instance_name = args.get("instance")
    pool = get_client_pool()
    registry = get_instance_registry()

    if instance_name:
        config = registry.get(instance_name)
        if config is None:
            return json.dumps({
                "error": f"Instance '{instance_name}' not found.",
                "available": registry.instance_names(),
            })
        health = pool.get_health(instance_name)
        health["environment"] = config.environment
        health["host"] = config.host
        return json.dumps(health, indent=2)

    # All instances
    results = []
    for name in registry.instance_names():
        config = registry.get(name)
        health = pool.get_health(name)
        if config:
            health["environment"] = config.environment
            health["host"] = config.host
        results.append(health)

    return json.dumps({"instances": results, "total": len(results)}, indent=2)


async def _handle_query_all_instances(
    args: dict[str, Any],
    tool_handlers: dict[str, Callable[..., Awaitable[str]]],
    write_tool_names: set[str],
) -> str:
    """Broadcast a tool call to multiple instances and aggregate results."""
    tool_name = args.get("tool_name", "")
    tool_args = args.get("tool_args", {})
    filter_environment = args.get("filter_environment")
    filter_labels = args.get("filter_labels")

    if not tool_name:
        return json.dumps({"error": "Missing required argument: tool_name"})

    # Reject write tools
    if tool_name in write_tool_names:
        return json.dumps({
            "error": f"Tool '{tool_name}' is a write operation and cannot be broadcast.",
            "detail": "query_all_instances only supports read-only tools.",
        })

    # Validate tool exists
    handler = tool_handlers.get(tool_name)
    if handler is None:
        return json.dumps({
            "error": f"Tool '{tool_name}' not found.",
            "available_read_tools": sorted(set(tool_handlers.keys()) - write_tool_names),
        })

    registry = get_instance_registry()
    config = registry.config

    # Get target instances
    instances = registry.list_instances(
        environment=filter_environment,
        labels=filter_labels,
    )

    if not instances:
        return json.dumps({
            "error": "No instances match the specified filters.",
            "filter_environment": filter_environment,
            "filter_labels": filter_labels,
        })

    # Apply broadcast_max_instances limit
    max_instances = config.broadcast_max_instances
    if len(instances) > max_instances:
        instances = instances[:max_instances]
        logger.warning(
            "Broadcast limited to %d instances (configured max: %d)",
            max_instances,
            max_instances,
        )

    timeout = config.broadcast_timeout_seconds
    pool = get_client_pool()

    async def _query_instance(inst_name: str) -> tuple[str, dict[str, Any]]:
        """Execute tool on a single instance."""
        try:
            # Get client for this instance (this may create it)
            client = await pool.get_client(inst_name)

            # Check circuit breaker before executing
            if client.circuit_breaker.state == CircuitState.OPEN:
                return inst_name, {"error": "circuit breaker open", "status": "unavailable"}

            # Import here to set up the client override for this call
            from ariaops_mcp.client import reset_client_override, set_client_override

            # Execute the tool with this instance's client
            # We override the default client for the duration of this call
            token = set_client_override(client)
            try:
                # Add instance to args for tools that use resolve_client
                call_args = {**tool_args, "instance": inst_name}
                result_str = await asyncio.wait_for(handler(call_args), timeout=timeout)
                try:
                    return inst_name, json.loads(result_str)
                except (json.JSONDecodeError, TypeError):
                    return inst_name, {"raw_result": result_str}
            finally:
                reset_client_override(token)

        except CircuitOpenError:
            return inst_name, {"error": "circuit breaker open", "status": "unavailable"}
        except TimeoutError:
            return inst_name, {"error": f"timeout after {timeout}s", "status": "timeout"}
        except Exception as e:
            return inst_name, {"error": str(e), "status": "error"}

    # Fan out to all instances concurrently
    start_time = time.monotonic()
    tasks = [_query_instance(inst.name) for inst in instances]
    results_list = await asyncio.gather(*tasks, return_exceptions=False)

    duration_ms = (time.monotonic() - start_time) * 1000

    # Build response
    results: dict[str, Any] = {}
    succeeded = 0
    failed = 0
    for inst_name, result in results_list:
        results[inst_name] = result
        if "error" in result:
            failed += 1
        else:
            succeeded += 1

    return json.dumps({
        "tool": tool_name,
        "results": results,
        "summary": {
            "queried": len(results_list),
            "succeeded": succeeded,
            "failed": failed,
            "duration_ms": round(duration_ms),
        },
    }, indent=2, default=str)


# ── Handler Registry ──────────────────────────────────────────────────────────


def tool_handlers() -> dict[str, Callable[[dict[str, Any]], Any]]:
    """Return handlers for simple meta-tools (list/select/health).

    Note: query_all_instances is handled separately in server.py because
    it needs access to the full tool handler registry.
    """
    return {
        "list_instances": _handle_list_instances,
        "select_instance": _handle_select_instance,
        "instance_health": _handle_instance_health,
    }


# Expose the broadcast handler for server.py to wire up
handle_query_all_instances = _handle_query_all_instances
