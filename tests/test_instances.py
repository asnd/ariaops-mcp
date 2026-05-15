"""Tests for multi-instance configuration, registry, and client pool."""

from pathlib import Path

import pytest

from ariaops_mcp.instances import (
    InstanceConfig,
    InstanceRegistry,
    get_instance_registry,
    reset_instance_registry,
)

# ── InstanceConfig Tests ──────────────────────────────────────────────────────


class TestInstanceConfig:
    def test_basic_creation(self):
        config = InstanceConfig(name="prod", host="vrops.example.com")
        assert config.name == "prod"
        assert config.host == "vrops.example.com"
        assert config.auth_source == "local"
        assert config.environment == "production"
        assert config.write_enabled is False
        assert config.verify_ssl is True

    def test_base_url(self):
        config = InstanceConfig(name="prod", host="vrops.example.com")
        assert config.base_url == "https://vrops.example.com/suite-api/api"

    def test_host_rejects_scheme(self):
        with pytest.raises(ValueError, match="hostname only"):
            InstanceConfig(name="prod", host="https://vrops.example.com")

    def test_get_username_from_instance_env(self, monkeypatch):
        monkeypatch.setenv("ARIAOPS_PROD_USERNAME", "prod-user")
        monkeypatch.setenv("ARIAOPS_USERNAME", "fallback-user")
        config = InstanceConfig(name="prod", host="vrops.example.com")
        assert config.get_username() == "prod-user"

    def test_get_username_fallback(self, monkeypatch):
        monkeypatch.delenv("ARIAOPS_PROD_USERNAME", raising=False)
        monkeypatch.setenv("ARIAOPS_USERNAME", "fallback-user")
        config = InstanceConfig(name="prod", host="vrops.example.com")
        assert config.get_username() == "fallback-user"

    def test_get_password_from_instance_env(self, monkeypatch):
        monkeypatch.setenv("ARIAOPS_PROD_FI_PASSWORD", "fi-pass")
        config = InstanceConfig(name="prod-fi", host="vrops-fi.example.com")
        assert config.get_password() == "fi-pass"

    def test_labels(self):
        config = InstanceConfig(
            name="prod",
            host="vrops.example.com",
            labels={"countries": ["SE", "FI", "NO"]},
        )
        assert "SE" in config.labels["countries"]

    def test_circuit_breaker_overrides(self):
        config = InstanceConfig(
            name="prod",
            host="vrops.example.com",
            cb_failure_threshold=10,
            cb_recovery_timeout=60,
        )
        assert config.cb_failure_threshold == 10
        assert config.cb_recovery_timeout == 60
        assert config.cb_success_threshold is None  # Uses default


# ── InstanceRegistry Tests ────────────────────────────────────────────────────


class TestInstanceRegistry:
    def test_load_from_yaml(self, tmp_path):
        yaml_content = """
default_instance: prod
broadcast_timeout_seconds: 45
broadcast_max_instances: 5
instances:
  prod:
    host: vrops-prod.example.com
    environment: production
    write_enabled: false
    description: "Production"
    labels:
      countries: [SE, FI]
  testbed:
    host: vrops-lab.example.com
    environment: testbed
    write_enabled: true
    description: "Lab"
"""
        config_file = tmp_path / "instances.yaml"
        config_file.write_text(yaml_content)

        registry = InstanceRegistry()
        registry.load(config_file)

        assert registry.is_loaded
        assert len(registry.config.instances) == 2
        assert registry.get_default_instance_name() == "prod"
        assert registry.config.broadcast_timeout_seconds == 45
        assert registry.config.broadcast_max_instances == 5

        prod = registry.get("prod")
        assert prod is not None
        assert prod.host == "vrops-prod.example.com"
        assert prod.environment == "production"
        assert prod.write_enabled is False
        assert "SE" in prod.labels["countries"]

        testbed = registry.get("testbed")
        assert testbed is not None
        assert testbed.write_enabled is True

    def test_load_from_legacy_env(self, monkeypatch):
        monkeypatch.setenv("ARIAOPS_HOST", "vrops-legacy.example.com")
        monkeypatch.setenv("ARIAOPS_AUTH_SOURCE", "ldap")
        monkeypatch.setenv("ARIAOPS_VERIFY_SSL", "false")
        monkeypatch.setenv("ARIAOPS_ENABLE_WRITE_OPERATIONS", "true")

        registry = InstanceRegistry()
        registry.load(Path("/nonexistent/instances.yaml"))

        assert registry.is_loaded
        assert len(registry.config.instances) == 1
        assert registry.get_default_instance_name() == "default"

        default = registry.get("default")
        assert default is not None
        assert default.host == "vrops-legacy.example.com"
        assert default.auth_source == "ldap"
        assert default.verify_ssl is False
        assert default.write_enabled is True

    def test_load_empty_no_host(self, monkeypatch):
        monkeypatch.delenv("ARIAOPS_HOST", raising=False)
        registry = InstanceRegistry()
        registry.load(Path("/nonexistent/instances.yaml"))
        assert registry.is_loaded
        assert len(registry.config.instances) == 0

    def test_env_override_host(self, tmp_path, monkeypatch):
        yaml_content = """
instances:
  prod:
    host: original.example.com
    environment: production
"""
        config_file = tmp_path / "instances.yaml"
        config_file.write_text(yaml_content)
        monkeypatch.setenv("ARIAOPS_PROD_HOST", "overridden.example.com")

        registry = InstanceRegistry()
        registry.load(config_file)

        prod = registry.get("prod")
        assert prod is not None
        assert prod.host == "overridden.example.com"

    def test_env_override_default_instance(self, tmp_path, monkeypatch):
        yaml_content = """
default_instance: prod
instances:
  prod:
    host: prod.example.com
    environment: production
  testbed:
    host: lab.example.com
    environment: testbed
"""
        config_file = tmp_path / "instances.yaml"
        config_file.write_text(yaml_content)
        monkeypatch.setenv("ARIAOPS_DEFAULT_INSTANCE", "testbed")

        registry = InstanceRegistry()
        registry.load(config_file)

        assert registry.get_default_instance_name() == "testbed"

    def test_list_instances_filter_environment(self, tmp_path):
        yaml_content = """
instances:
  prod:
    host: prod.example.com
    environment: production
  preprod:
    host: preprod.example.com
    environment: preproduction
  testbed:
    host: lab.example.com
    environment: testbed
"""
        config_file = tmp_path / "instances.yaml"
        config_file.write_text(yaml_content)

        registry = InstanceRegistry()
        registry.load(config_file)

        prod_instances = registry.list_instances(environment="production")
        assert len(prod_instances) == 1
        assert prod_instances[0].name == "prod"

    def test_list_instances_filter_labels(self, tmp_path):
        yaml_content = """
instances:
  prod-se:
    host: prod-se.example.com
    environment: production
    labels:
      countries: [SE, DK]
  prod-fi:
    host: prod-fi.example.com
    environment: production
    labels:
      countries: [FI]
"""
        config_file = tmp_path / "instances.yaml"
        config_file.write_text(yaml_content)

        registry = InstanceRegistry()
        registry.load(config_file)

        fi_instances = registry.list_instances(labels={"countries": ["FI"]})
        assert len(fi_instances) == 1
        assert fi_instances[0].name == "prod-fi"

        nordic_instances = registry.list_instances(labels={"countries": ["SE"]})
        assert len(nordic_instances) == 1
        assert nordic_instances[0].name == "prod-se"

    def test_instance_names(self, tmp_path):
        yaml_content = """
instances:
  alpha:
    host: alpha.example.com
    environment: production
  beta:
    host: beta.example.com
    environment: testbed
"""
        config_file = tmp_path / "instances.yaml"
        config_file.write_text(yaml_content)

        registry = InstanceRegistry()
        registry.load(config_file)

        names = registry.instance_names()
        assert "alpha" in names
        assert "beta" in names

    def test_get_nonexistent_instance(self, tmp_path):
        yaml_content = """
instances:
  prod:
    host: prod.example.com
    environment: production
"""
        config_file = tmp_path / "instances.yaml"
        config_file.write_text(yaml_content)

        registry = InstanceRegistry()
        registry.load(config_file)

        assert registry.get("nonexistent") is None


# ── Singleton Tests ───────────────────────────────────────────────────────────


class TestRegistrySingleton:
    def test_get_instance_registry_returns_singleton(self, monkeypatch):
        monkeypatch.setenv("ARIAOPS_HOST", "vrops.test.local")
        reset_instance_registry()
        r1 = get_instance_registry()
        r2 = get_instance_registry()
        assert r1 is r2

    def test_reset_clears_singleton(self, monkeypatch):
        monkeypatch.setenv("ARIAOPS_HOST", "vrops.test.local")
        reset_instance_registry()
        r1 = get_instance_registry()
        reset_instance_registry()
        r2 = get_instance_registry()
        assert r1 is not r2
