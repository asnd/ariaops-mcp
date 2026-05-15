"""Instance resolver — determines which vROps instance a tool call should target.

Resolution chain:
1. Explicit `instance` parameter on the tool call
2. Session-level default (ContextVar set by `select_instance` meta-tool)
3. Server-level default (from instances.yaml or ARIAOPS_DEFAULT_INSTANCE)
4. Error if no instance can be resolved
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token

from ariaops_mcp.instances import InstanceConfig, get_instance_registry

logger = logging.getLogger(__name__)

# Session-level default instance (set by select_instance tool)
_session_instance: ContextVar[str | None] = ContextVar("ariaops_session_instance", default=None)


class InstanceResolutionError(Exception):
    """Raised when no instance can be resolved for a tool call."""

    pass


class InstanceAccessDeniedError(Exception):
    """Raised when a user lacks permission to access the requested instance."""

    pass


def set_session_instance(name: str) -> Token[str | None]:
    """Set the session-level default instance. Returns reset token."""
    return _session_instance.set(name)


def get_session_instance() -> str | None:
    """Get the current session-level default instance."""
    return _session_instance.get()


def reset_session_instance(token: Token[str | None]) -> None:
    """Reset the session-level default instance."""
    _session_instance.reset(token)


def clear_session_instance() -> Token[str | None]:
    """Clear the session-level default instance."""
    return _session_instance.set(None)


def resolve_instance_name(
    explicit: str | None = None,
    user_scopes: set[str] | None = None,
) -> str:
    """Resolve which instance to use for a tool call.

    Args:
        explicit: Instance name passed directly in tool args.
        user_scopes: OAuth scopes from the authenticated user (None = no auth / all access).

    Returns:
        Resolved instance name.

    Raises:
        InstanceResolutionError: If no instance can be determined.
        InstanceAccessDeniedError: If the user doesn't have access to the resolved instance.
    """
    registry = get_instance_registry()

    # Resolution chain
    name = explicit or _session_instance.get() or registry.get_default_instance_name()

    if not name:
        available = registry.instance_names()
        if len(available) == 1:
            # Auto-select the only configured instance
            name = available[0]
        else:
            raise InstanceResolutionError(
                "No instance specified and no default configured. "
                f"Available instances: {available}. "
                "Use 'select_instance' to set a session default or pass 'instance' parameter."
            )

    # Validate instance exists
    instance = registry.get(name)
    if instance is None:
        available = registry.instance_names()
        raise InstanceResolutionError(
            f"Instance '{name}' not found. Available: {available}"
        )

    # Check access if OAuth scopes are provided
    if user_scopes is not None:
        if not check_instance_access(user_scopes, name, write=False):
            raise InstanceAccessDeniedError(
                f"Access denied to instance '{name}'. "
                "Required scope: ariaops:{instance}:read or ariaops:*:read"
            )

    return name


def resolve_instance_config(
    explicit: str | None = None,
    user_scopes: set[str] | None = None,
) -> InstanceConfig:
    """Resolve and return the full instance config."""
    name = resolve_instance_name(explicit, user_scopes)
    registry = get_instance_registry()
    config = registry.get(name)
    assert config is not None  # Already validated in resolve_instance_name
    return config


def check_instance_access(
    user_scopes: set[str],
    instance: str,
    write: bool = False,
) -> bool:
    """Check if user scopes grant access to the specified instance.

    Scope format: ariaops:{instance}:{access}
    Where access is 'read' or 'write' (write implies read).
    Wildcard '*' matches all instances.
    """
    required_access = "write" if write else "read"

    # Direct match
    if f"ariaops:{instance}:{required_access}" in user_scopes:
        return True

    # Wildcard match
    if f"ariaops:*:{required_access}" in user_scopes:
        return True

    # Write implies read
    if required_access == "read":
        if f"ariaops:{instance}:write" in user_scopes:
            return True
        if "ariaops:*:write" in user_scopes:
            return True

    return False


def check_write_access(
    instance_name: str,
    user_scopes: set[str] | None = None,
) -> str | None:
    """Check if write operations are allowed on the instance.

    Returns error message if denied, None if allowed.
    """
    registry = get_instance_registry()
    instance = registry.get(instance_name)
    if instance is None:
        return f"Instance '{instance_name}' not found."

    if not instance.write_enabled:
        return (
            f"Write operations are disabled on instance '{instance_name}' "
            f"(environment: {instance.environment}). "
            "Set write_enabled: true in instances.yaml to enable."
        )

    if user_scopes is not None:
        if not check_instance_access(user_scopes, instance_name, write=True):
            return (
                f"Access denied: write operations on instance '{instance_name}' "
                "require scope ariaops:{instance}:write or ariaops:*:write."
            )

    return None


def get_accessible_instances(user_scopes: set[str]) -> dict[str, str]:
    """Return a dict of instance_name -> max_access_level for the given scopes.

    Returns:
        Mapping of instance name to access level ('read' or 'write').
    """
    registry = get_instance_registry()
    result: dict[str, str] = {}

    for name in registry.instance_names():
        if check_instance_access(user_scopes, name, write=True):
            result[name] = "write"
        elif check_instance_access(user_scopes, name, write=False):
            result[name] = "read"

    return result
