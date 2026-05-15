"""Tests for the instance resolver."""

import pytest

from ariaops_mcp.instances import reset_instance_registry
from ariaops_mcp.resolver import (
    InstanceResolutionError,
    check_instance_access,
    check_write_access,
    clear_session_instance,
    get_accessible_instances,
    get_session_instance,
    resolve_instance_name,
    set_session_instance,
)


@pytest.fixture
def multi_instance_registry(monkeypatch, tmp_path):
    """Set up a multi-instance registry for tests."""
    yaml_content = """
default_instance: prod
instances:
  prod:
    host: prod.example.com
    environment: production
    write_enabled: false
    labels:
      countries: [SE, DK]
  prod-fi:
    host: prod-fi.example.com
    environment: production
    write_enabled: false
    labels:
      countries: [FI]
  testbed:
    host: lab.example.com
    environment: testbed
    write_enabled: true
"""
    config_file = tmp_path / "instances.yaml"
    config_file.write_text(yaml_content)
    monkeypatch.setenv("ARIAOPS_INSTANCES_FILE", str(config_file))
    monkeypatch.setenv("ARIAOPS_USERNAME", "testuser")
    monkeypatch.setenv("ARIAOPS_PASSWORD", "testpass")
    reset_instance_registry()


# ── Resolution Chain Tests ────────────────────────────────────────────────────


class TestResolveInstanceName:
    def test_explicit_takes_priority(self, multi_instance_registry):
        name = resolve_instance_name(explicit="testbed")
        assert name == "testbed"

    def test_session_default_used_when_no_explicit(self, multi_instance_registry):
        set_session_instance("prod-fi")
        try:
            name = resolve_instance_name()
            assert name == "prod-fi"
        finally:
            clear_session_instance()

    def test_server_default_used_when_no_session(self, multi_instance_registry):
        name = resolve_instance_name()
        assert name == "prod"  # default_instance in yaml

    def test_explicit_overrides_session(self, multi_instance_registry):
        set_session_instance("prod-fi")
        try:
            name = resolve_instance_name(explicit="testbed")
            assert name == "testbed"
        finally:
            clear_session_instance()

    def test_nonexistent_instance_raises(self, multi_instance_registry):
        with pytest.raises(InstanceResolutionError, match="not found"):
            resolve_instance_name(explicit="nonexistent")

    def test_auto_select_single_instance(self, monkeypatch, tmp_path):
        """When only one instance is configured and no default, auto-select it."""
        yaml_content = """
instances:
  only-one:
    host: single.example.com
    environment: testbed
"""
        config_file = tmp_path / "instances.yaml"
        config_file.write_text(yaml_content)
        monkeypatch.setenv("ARIAOPS_INSTANCES_FILE", str(config_file))
        monkeypatch.setenv("ARIAOPS_USERNAME", "testuser")
        monkeypatch.setenv("ARIAOPS_PASSWORD", "testpass")
        reset_instance_registry()

        name = resolve_instance_name()
        assert name == "only-one"

    def test_no_default_multiple_instances_raises(self, monkeypatch, tmp_path):
        """When multiple instances exist and no default, raise error."""
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
        monkeypatch.setenv("ARIAOPS_INSTANCES_FILE", str(config_file))
        monkeypatch.setenv("ARIAOPS_USERNAME", "testuser")
        monkeypatch.setenv("ARIAOPS_PASSWORD", "testpass")
        reset_instance_registry()

        with pytest.raises(InstanceResolutionError, match="No instance specified"):
            resolve_instance_name()


# ── OAuth Scope Access Tests ──────────────────────────────────────────────────


class TestCheckInstanceAccess:
    def test_direct_read_scope(self):
        assert check_instance_access({"ariaops:prod:read"}, "prod", write=False) is True

    def test_direct_write_scope(self):
        assert check_instance_access({"ariaops:prod:write"}, "prod", write=True) is True

    def test_write_implies_read(self):
        assert check_instance_access({"ariaops:prod:write"}, "prod", write=False) is True

    def test_read_does_not_imply_write(self):
        assert check_instance_access({"ariaops:prod:read"}, "prod", write=True) is False

    def test_wildcard_read(self):
        assert check_instance_access({"ariaops:*:read"}, "prod", write=False) is True
        assert check_instance_access({"ariaops:*:read"}, "testbed", write=False) is True

    def test_wildcard_write(self):
        assert check_instance_access({"ariaops:*:write"}, "prod", write=True) is True
        assert check_instance_access({"ariaops:*:write"}, "prod", write=False) is True

    def test_wrong_instance_denied(self):
        assert check_instance_access({"ariaops:prod:read"}, "testbed", write=False) is False

    def test_empty_scopes_denied(self):
        assert check_instance_access(set(), "prod", write=False) is False

    def test_unrelated_scopes_denied(self):
        assert check_instance_access({"mcp:read", "profile"}, "prod", write=False) is False


class TestCheckWriteAccess:
    def test_write_disabled_instance(self, multi_instance_registry):
        error = check_write_access("prod")
        assert error is not None
        assert "disabled" in error

    def test_write_enabled_instance(self, multi_instance_registry):
        error = check_write_access("testbed")
        assert error is None

    def test_write_with_scope_enforcement(self, multi_instance_registry):
        # Testbed has write_enabled, user has write scope
        error = check_write_access("testbed", user_scopes={"ariaops:testbed:write"})
        assert error is None

    def test_write_scope_denied(self, multi_instance_registry):
        # Testbed has write_enabled, but user only has read
        error = check_write_access("testbed", user_scopes={"ariaops:testbed:read"})
        assert error is not None
        assert "require scope" in error

    def test_nonexistent_instance(self, multi_instance_registry):
        error = check_write_access("nonexistent")
        assert error is not None
        assert "not found" in error


class TestGetAccessibleInstances:
    def test_wildcard_read(self, multi_instance_registry):
        result = get_accessible_instances({"ariaops:*:read"})
        assert "prod" in result
        assert "prod-fi" in result
        assert "testbed" in result
        assert all(v == "read" for v in result.values())

    def test_wildcard_write(self, multi_instance_registry):
        result = get_accessible_instances({"ariaops:*:write"})
        assert all(v == "write" for v in result.values())

    def test_specific_instance(self, multi_instance_registry):
        result = get_accessible_instances({"ariaops:prod:read", "ariaops:testbed:write"})
        assert result.get("prod") == "read"
        assert result.get("testbed") == "write"
        assert "prod-fi" not in result

    def test_empty_scopes(self, multi_instance_registry):
        result = get_accessible_instances(set())
        assert result == {}


# ── Session Instance Tests ────────────────────────────────────────────────────


class TestSessionInstance:
    def test_set_and_get(self):
        set_session_instance("prod-fi")
        try:
            assert get_session_instance() == "prod-fi"
        finally:
            clear_session_instance()

    def test_clear(self):
        set_session_instance("prod-fi")
        clear_session_instance()
        assert get_session_instance() is None
