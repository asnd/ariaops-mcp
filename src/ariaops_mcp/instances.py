"""Multi-instance configuration, registry, and client pool.

Supports multiple vROps API endpoints with per-instance credentials,
write-enable toggles, and independent circuit breakers.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# ── Models ────────────────────────────────────────────────────────────────────


class InstanceConfig(BaseModel):
    """Configuration for a single vROps instance."""

    name: str
    host: str
    auth_source: str = "local"
    environment: Literal["production", "preproduction", "testbed"] = "production"
    write_enabled: bool = False
    verify_ssl: bool = True
    description: str = ""
    labels: dict[str, list[str]] = Field(default_factory=dict)

    # Circuit breaker overrides (None = use server defaults)
    cb_failure_threshold: int | None = None
    cb_recovery_timeout: int | None = None
    cb_success_threshold: int | None = None

    @field_validator("host")
    @classmethod
    def host_must_not_include_scheme(cls, value: str) -> str:
        if "://" in value:
            raise ValueError("host should be hostname only (no scheme)")
        return value

    @property
    def base_url(self) -> str:
        return f"https://{self.host}/suite-api/api"

    def get_username(self) -> str:
        """Resolve username: instance-specific env var -> shared fallback."""
        env_key = f"ARIAOPS_{self.name.upper().replace('-', '_')}_USERNAME"
        return os.environ.get(env_key, os.environ.get("ARIAOPS_USERNAME", ""))

    def get_password(self) -> str:
        """Resolve password: instance-specific env var -> shared fallback."""
        env_key = f"ARIAOPS_{self.name.upper().replace('-', '_')}_PASSWORD"
        return os.environ.get(env_key, os.environ.get("ARIAOPS_PASSWORD", ""))

    def get_auth_source(self) -> str:
        """Resolve auth_source: instance-specific env var -> config value."""
        env_key = f"ARIAOPS_{self.name.upper().replace('-', '_')}_AUTH_SOURCE"
        return os.environ.get(env_key, self.auth_source)


class InstancesConfig(BaseModel):
    """Top-level multi-instance configuration."""

    default_instance: str | None = None
    broadcast_timeout_seconds: int = Field(default=30, ge=1, le=300)
    broadcast_max_instances: int = Field(default=10, ge=1, le=50)
    instances: dict[str, InstanceConfig] = Field(default_factory=dict)


# ── Registry ──────────────────────────────────────────────────────────────────


class InstanceRegistry:
    """Registry of configured vROps instances.

    Loads from instances.yaml or falls back to legacy single-instance
    configuration from ARIAOPS_HOST/USERNAME/PASSWORD env vars.
    """

    def __init__(self) -> None:
        self._config: InstancesConfig = InstancesConfig()
        self._loaded = False

    @property
    def config(self) -> InstancesConfig:
        return self._config

    def load(self, config_path: Path | None = None) -> None:
        """Load instance configuration from YAML file or legacy env vars.

        Args:
            config_path: Path to instances.yaml. If None, uses
                         ARIAOPS_INSTANCES_FILE env var or ./instances.yaml.
        """
        if config_path is None:
            config_path = Path(os.environ.get("ARIAOPS_INSTANCES_FILE", "instances.yaml"))

        if config_path.is_file():
            self._load_from_yaml(config_path)
        else:
            self._load_from_legacy_env()

        self._apply_env_overrides()
        self._loaded = True

        if not self._config.instances:
            logger.warning("No instances configured. Check instances.yaml or ARIAOPS_HOST env var.")
        else:
            logger.info(
                "Instance registry loaded: %d instance(s) [%s]",
                len(self._config.instances),
                ", ".join(self._config.instances.keys()),
            )

    def _load_from_yaml(self, path: Path) -> None:
        """Load from instances.yaml."""
        logger.info("Loading instance configuration from %s", path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"instances.yaml must be a YAML mapping, got {type(raw).__name__}")

        instances: dict[str, InstanceConfig] = {}
        raw_instances = raw.get("instances", {})
        for name, inst_data in raw_instances.items():
            if not isinstance(inst_data, dict):
                raise ValueError(f"Instance '{name}' config must be a mapping")
            inst_data["name"] = name
            instances[name] = InstanceConfig(**inst_data)

        self._config = InstancesConfig(
            default_instance=raw.get("default_instance"),
            broadcast_timeout_seconds=raw.get("broadcast_timeout_seconds", 30),
            broadcast_max_instances=raw.get("broadcast_max_instances", 10),
            instances=instances,
        )

    def _load_from_legacy_env(self) -> None:
        """Fall back to single-instance config from ARIAOPS_HOST env var."""
        host = os.environ.get("ARIAOPS_HOST")
        if not host:
            logger.debug("No instances.yaml and no ARIAOPS_HOST; registry will be empty.")
            return

        logger.info("No instances.yaml found; using legacy ARIAOPS_HOST env var (single-instance mode)")
        verify_ssl_str = os.environ.get("ARIAOPS_VERIFY_SSL", "true")
        verify_ssl = verify_ssl_str.lower() not in ("false", "0", "no")
        write_enabled_str = os.environ.get("ARIAOPS_ENABLE_WRITE_OPERATIONS", "false")
        write_enabled = write_enabled_str.lower() in ("true", "1", "yes")

        instance = InstanceConfig(
            name="default",
            host=host,
            auth_source=os.environ.get("ARIAOPS_AUTH_SOURCE", "local"),
            environment="production",
            write_enabled=write_enabled,
            verify_ssl=verify_ssl,
            description="Default instance (from ARIAOPS_HOST)",
        )
        self._config = InstancesConfig(
            default_instance="default",
            instances={"default": instance},
        )

    def _apply_env_overrides(self) -> None:
        """Apply env var overrides for host per instance."""
        for name, inst in self._config.instances.items():
            env_key = f"ARIAOPS_{name.upper().replace('-', '_')}_HOST"
            host_override = os.environ.get(env_key)
            if host_override:
                inst.host = host_override
                logger.debug("Instance '%s' host overridden to '%s' via %s", name, host_override, env_key)

        # Default instance override
        default_override = os.environ.get("ARIAOPS_DEFAULT_INSTANCE")
        if default_override:
            self._config.default_instance = default_override

    def get(self, name: str) -> InstanceConfig | None:
        """Get instance config by name."""
        return self._config.instances.get(name)

    def list_instances(
        self,
        environment: str | None = None,
        labels: dict[str, list[str]] | None = None,
    ) -> list[InstanceConfig]:
        """List instances, optionally filtered by environment or labels."""
        result = list(self._config.instances.values())

        if environment:
            result = [i for i in result if i.environment == environment]

        if labels:
            filtered = []
            for inst in result:
                match = True
                for key, values in labels.items():
                    inst_values = inst.labels.get(key, [])
                    if not any(v in inst_values for v in values):
                        match = False
                        break
                if match:
                    filtered.append(inst)
            result = filtered

        return result

    def get_default_instance_name(self) -> str | None:
        """Return the configured default instance name."""
        return self._config.default_instance

    def instance_names(self) -> list[str]:
        """Return all instance names."""
        return list(self._config.instances.keys())

    @property
    def is_loaded(self) -> bool:
        return self._loaded


# ── Module-level singleton ────────────────────────────────────────────────────

_registry: InstanceRegistry | None = None


def get_instance_registry() -> InstanceRegistry:
    """Get or create the module-level instance registry singleton."""
    global _registry
    if _registry is None:
        _registry = InstanceRegistry()
        _registry.load()
    return _registry


def reset_instance_registry() -> None:
    """Reset the registry singleton (for testing)."""
    global _registry
    _registry = None
