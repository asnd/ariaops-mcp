"""Role-based resolution of which Aria Operations instance(s) a caller may use.

Two roles are supported:

* **ops** — operations users may access *all* configured instances. They select
  a target instance per request via the ``instance`` tool argument (or the
  configured default when only one instance exists).
* **country** — country users are pinned to a *single* instance, derived from
  their country/instance claim. They cannot reach any other instance.

On the HTTP transport the role/country/instance are read from the validated JWT
claims; on stdio (local) use they fall back to the ``ARIAOPS_DEFAULT_*`` settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ariaops_mcp.config import Settings, get_settings


class AccessDenied(Exception):
    """Raised when a principal is not allowed to use the requested instance."""


@dataclass(frozen=True)
class Principal:
    role: str
    instance_ids: tuple[str, ...]
    default_instance_id: str | None

    def can_access(self, instance_id: str) -> bool:
        return instance_id in self.instance_ids

    def resolve_instance(self, requested: str | None) -> str:
        """Return the effective instance id for this request, enforcing access."""
        if requested is not None:
            if requested not in self.instance_ids:
                raise AccessDenied(
                    f"Instance '{requested}' is not accessible for role '{self.role}'. "
                    f"Accessible instances: {', '.join(self.instance_ids) or '(none)'}"
                )
            return requested
        if self.default_instance_id is not None:
            return self.default_instance_id
        if len(self.instance_ids) == 1:
            return self.instance_ids[0]
        raise AccessDenied(
            "Multiple instances are available; specify the 'instance' argument. "
            f"Accessible instances: {', '.join(self.instance_ids)}"
        )


def _claim_contains(value: Any, expected: str) -> bool:
    """Return True if a claim (string or list) matches/contains ``expected``."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() == expected.strip().lower()
    if isinstance(value, (list, tuple, set)):
        return any(_claim_contains(item, expected) for item in value)
    return str(value).strip().lower() == expected.strip().lower()


def _resolve_role(value: Any, settings: Settings) -> str | None:
    """Map a raw role claim to the canonical 'ops' or 'country' role, if any."""
    if _claim_contains(value, settings.ops_role):
        return "ops"
    if _claim_contains(value, settings.country_role):
        return "country"
    return None


def _instance_for_country(country: str, settings: Settings) -> str:
    matches = [
        inst.id
        for inst in settings.resolved_instances()
        if inst.country and inst.country.strip().lower() == country.strip().lower()
    ]
    if not matches:
        raise AccessDenied(f"No Aria Operations instance is configured for country '{country}'")
    if len(matches) > 1:
        raise AccessDenied(
            f"Country '{country}' maps to multiple instances ({', '.join(matches)}); "
            "the user's token must specify an explicit instance claim"
        )
    return matches[0]


def resolve_principal(
    claims: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> Principal:
    """Build the :class:`Principal` for the current caller.

    ``claims`` are the validated JWT claims (HTTP transport) or ``None`` for
    stdio/local use, in which case the ``ARIAOPS_DEFAULT_*`` settings apply.
    """
    settings = settings or get_settings()
    all_ids = tuple(inst.id for inst in settings.resolved_instances())

    if claims is None:
        role = _resolve_role(settings.default_role, settings) or settings.default_role.lower()
        country = settings.default_country
        explicit_instance = settings.default_instance
    else:
        raw_role = claims.get(settings.role_claim)
        if raw_role is None:
            # No role claim present — fall back to the configured default role.
            role = _resolve_role(settings.default_role, settings) or settings.default_role.lower()
        else:
            # A role was asserted: map it strictly so an unrecognized role is
            # denied rather than silently inheriting the (often broader) default.
            mapped = _resolve_role(raw_role, settings)
            role = mapped if mapped is not None else str(raw_role)
        country = claims.get(settings.country_claim) or settings.default_country
        explicit_instance = claims.get(settings.instance_claim)

    if role == "ops":
        # Ops users see every instance; default only when one exists / configured.
        default_id: str | None
        if settings.default_instance:
            default_id = settings.default_instance
        elif len(all_ids) == 1:
            default_id = all_ids[0]
        else:
            default_id = None
        return Principal(role="ops", instance_ids=all_ids, default_instance_id=default_id)

    if role == "country":
        if explicit_instance:
            if explicit_instance not in all_ids:
                raise AccessDenied(f"Instance claim '{explicit_instance}' does not match any configured instance")
            target = str(explicit_instance)
        elif country:
            target = _instance_for_country(str(country), settings)
        else:
            raise AccessDenied(
                "Country-role user has no country or instance claim; cannot determine the target instance"
            )
        return Principal(role="country", instance_ids=(target,), default_instance_id=target)

    raise AccessDenied(
        f"Unknown role '{role}'. Expected '{settings.ops_role}' or '{settings.country_role}'."
    )
