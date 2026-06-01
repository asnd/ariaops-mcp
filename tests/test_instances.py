"""Tests for multi-instance configuration, role-based access, and client registry."""

import json

import httpx
import pytest
import respx
from mcp.types import CallToolRequest, ListToolsRequest
from pydantic import ValidationError

import ariaops_mcp.client as client_module
from ariaops_mcp.config import Settings
from ariaops_mcp.principal import AccessDenied, resolve_principal
from ariaops_mcp.server import create_server
from tests.conftest import TOKEN_RESPONSE

TWO_INSTANCES = json.dumps(
    [
        {"id": "us", "host": "us.vrops.local", "username": "u", "password": "p", "country": "US"},
        {"id": "de", "host": "de.vrops.local", "username": "u", "password": "p", "country": "DE"},
    ]
)


def _settings(**overrides):
    base = {
        "ARIAOPS_HOST": "vrops.test.local",
        "ARIAOPS_USERNAME": "testuser",
        "ARIAOPS_PASSWORD": "testpass",
    }
    base.update(overrides)
    return Settings.model_validate(base)


# --- Config parsing ---------------------------------------------------------


def test_legacy_single_instance_synthesized():
    s = _settings()
    instances = s.resolved_instances()
    assert len(instances) == 1
    assert instances[0].id == "default"
    assert instances[0].host == "vrops.test.local"
    assert s.default_instance_id == "default"
    assert s.base_url == "https://vrops.test.local/suite-api/api"


def test_instances_parsed_from_json():
    s = Settings.model_validate({"ARIAOPS_INSTANCES": TWO_INSTANCES})
    ids = [i.id for i in s.resolved_instances()]
    assert ids == ["us", "de"]
    assert s.get_instance("de").base_url == "https://de.vrops.local/suite-api/api"


def test_instances_without_legacy_vars_allowed(monkeypatch):
    # No ARIAOPS_HOST/USERNAME/PASSWORD, but instances provided.
    for var in ("ARIAOPS_HOST", "ARIAOPS_USERNAME", "ARIAOPS_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    s = Settings.model_validate({"ARIAOPS_INSTANCES": TWO_INSTANCES})
    assert s.host is None
    assert len(s.resolved_instances()) == 2


def test_missing_both_legacy_and_instances_rejected(monkeypatch):
    for var in ("ARIAOPS_HOST", "ARIAOPS_USERNAME", "ARIAOPS_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValidationError, match="ARIAOPS_INSTANCES"):
        Settings.model_validate({})


def test_duplicate_instance_ids_rejected():
    dup = json.dumps(
        [
            {"id": "us", "host": "a.local", "username": "u", "password": "p"},
            {"id": "us", "host": "b.local", "username": "u", "password": "p"},
        ]
    )
    with pytest.raises(ValidationError, match="Duplicate instance id"):
        Settings.model_validate({"ARIAOPS_INSTANCES": dup})


def test_unknown_default_instance_rejected():
    with pytest.raises(ValidationError, match="ARIAOPS_DEFAULT_INSTANCE"):
        Settings.model_validate({"ARIAOPS_INSTANCES": TWO_INSTANCES, "ARIAOPS_DEFAULT_INSTANCE": "fr"})


def test_instance_host_with_scheme_rejected():
    bad = json.dumps([{"id": "x", "host": "https://a.local", "username": "u", "password": "p"}])
    with pytest.raises(ValidationError, match="no scheme"):
        Settings.model_validate({"ARIAOPS_INSTANCES": bad})


def test_explicit_default_instance_used():
    s = Settings.model_validate({"ARIAOPS_INSTANCES": TWO_INSTANCES, "ARIAOPS_DEFAULT_INSTANCE": "de"})
    assert s.default_instance_id == "de"


# --- Principal resolution ---------------------------------------------------


def test_ops_role_sees_all_instances():
    s = Settings.model_validate({"ARIAOPS_INSTANCES": TWO_INSTANCES})
    p = resolve_principal(claims={"ariaops_role": "ops"}, settings=s)
    assert p.role == "ops"
    assert set(p.instance_ids) == {"us", "de"}
    assert p.resolve_instance("us") == "us"


def test_ops_role_requires_explicit_instance_when_multiple():
    s = Settings.model_validate({"ARIAOPS_INSTANCES": TWO_INSTANCES})
    p = resolve_principal(claims={"ariaops_role": "ops"}, settings=s)
    with pytest.raises(AccessDenied, match="specify the 'instance'"):
        p.resolve_instance(None)


def test_ops_role_single_instance_has_default():
    s = _settings()
    p = resolve_principal(claims={"ariaops_role": "ops"}, settings=s)
    assert p.resolve_instance(None) == "default"


def test_country_role_pinned_to_single_instance():
    s = Settings.model_validate({"ARIAOPS_INSTANCES": TWO_INSTANCES})
    p = resolve_principal(claims={"ariaops_role": "country", "ariaops_country": "DE"}, settings=s)
    assert p.role == "country"
    assert p.instance_ids == ("de",)
    assert p.resolve_instance(None) == "de"


def test_country_role_cannot_access_other_instance():
    s = Settings.model_validate({"ARIAOPS_INSTANCES": TWO_INSTANCES})
    p = resolve_principal(claims={"ariaops_role": "country", "ariaops_country": "DE"}, settings=s)
    with pytest.raises(AccessDenied, match="not accessible"):
        p.resolve_instance("us")


def test_country_role_explicit_instance_claim():
    s = Settings.model_validate({"ARIAOPS_INSTANCES": TWO_INSTANCES})
    p = resolve_principal(claims={"ariaops_role": "country", "ariaops_instance": "us"}, settings=s)
    assert p.instance_ids == ("us",)


def test_country_role_without_country_or_instance_denied():
    s = Settings.model_validate({"ARIAOPS_INSTANCES": TWO_INSTANCES})
    with pytest.raises(AccessDenied, match="no country or instance claim"):
        resolve_principal(claims={"ariaops_role": "country"}, settings=s)


def test_country_unknown_country_denied():
    s = Settings.model_validate({"ARIAOPS_INSTANCES": TWO_INSTANCES})
    with pytest.raises(AccessDenied, match="No Aria Operations instance"):
        resolve_principal(claims={"ariaops_role": "country", "ariaops_country": "FR"}, settings=s)


def test_role_claim_as_list():
    s = Settings.model_validate({"ARIAOPS_INSTANCES": TWO_INSTANCES})
    p = resolve_principal(claims={"ariaops_role": ["other", "ops"]}, settings=s)
    assert p.role == "ops"


def test_no_claims_uses_default_role():
    s = Settings.model_validate({"ARIAOPS_INSTANCES": TWO_INSTANCES, "ARIAOPS_DEFAULT_ROLE": "ops"})
    p = resolve_principal(claims=None, settings=s)
    assert p.role == "ops"
    assert set(p.instance_ids) == {"us", "de"}


def test_no_claims_country_default():
    s = Settings.model_validate(
        {
            "ARIAOPS_INSTANCES": TWO_INSTANCES,
            "ARIAOPS_DEFAULT_ROLE": "country",
            "ARIAOPS_DEFAULT_COUNTRY": "US",
        }
    )
    p = resolve_principal(claims=None, settings=s)
    assert p.role == "country"
    assert p.instance_ids == ("us",)


def test_unknown_role_denied():
    s = Settings.model_validate({"ARIAOPS_INSTANCES": TWO_INSTANCES, "ARIAOPS_DEFAULT_ROLE": "ops"})
    with pytest.raises(AccessDenied, match="Unknown role"):
        resolve_principal(claims={"ariaops_role": "viewer"}, settings=s)


# --- Client registry --------------------------------------------------------


def test_get_client_caches_per_instance(mock_env, monkeypatch):
    monkeypatch.setenv("ARIAOPS_INSTANCES", TWO_INSTANCES)
    from ariaops_mcp.config import clear_settings_cache

    clear_settings_cache()
    c_us = client_module.get_client("us")
    c_de = client_module.get_client("de")
    assert c_us is not c_de
    assert c_us.instance.host == "us.vrops.local"
    assert client_module.get_client("us") is c_us  # cached


def test_current_instance_contextvar(mock_env, monkeypatch):
    monkeypatch.setenv("ARIAOPS_INSTANCES", TWO_INSTANCES)
    from ariaops_mcp.config import clear_settings_cache

    clear_settings_cache()
    token = client_module.set_current_instance("de")
    try:
        assert client_module.get_client().instance.id == "de"
    finally:
        client_module.reset_current_instance(token)


@pytest.mark.asyncio
async def test_close_all_closes_cached_clients(mock_env, monkeypatch):
    monkeypatch.setenv("ARIAOPS_INSTANCES", TWO_INSTANCES)
    from ariaops_mcp.config import clear_settings_cache

    clear_settings_cache()
    client_module.get_client("us")
    client_module.get_client("de")
    assert len(client_module._clients) == 2
    await client_module.close_all()
    assert len(client_module._clients) == 0


# --- Dispatch-level instance selection --------------------------------------


@pytest.mark.asyncio
async def test_list_instances_tool(monkeypatch):
    monkeypatch.setenv("ARIAOPS_INSTANCES", TWO_INSTANCES)
    from ariaops_mcp.config import clear_settings_cache

    clear_settings_cache()
    server = create_server()
    result = await server.request_handlers[CallToolRequest](
        CallToolRequest(method="tools/call", params={"name": "list_instances", "arguments": {}})
    )
    data = json.loads(result.root.content[0].text)
    assert {i["id"] for i in data["instances"]} == {"us", "de"}


@pytest.mark.asyncio
async def test_tools_advertise_instance_arg(monkeypatch):
    monkeypatch.setenv("ARIAOPS_INSTANCES", TWO_INSTANCES)
    from ariaops_mcp.config import clear_settings_cache

    clear_settings_cache()
    server = create_server()
    result = await server.request_handlers[ListToolsRequest](
        ListToolsRequest(method="tools/list", params=None)
    )
    tools = {t.name: t for t in result.root.tools}
    assert "list_instances" in tools
    assert "instance" in tools["list_resources"].inputSchema["properties"]


@pytest.mark.asyncio
async def test_call_tool_routes_to_selected_instance(monkeypatch):
    monkeypatch.setenv("ARIAOPS_INSTANCES", TWO_INSTANCES)
    from ariaops_mcp.config import clear_settings_cache

    clear_settings_cache()
    de_base = "https://de.vrops.local/suite-api/api"
    with respx.mock:
        respx.post(f"{de_base}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        version_route = respx.get(f"{de_base}/versions/current").mock(
            return_value=httpx.Response(200, json={"releaseName": "8.18.0"})
        )

        server = create_server()
        result = await server.request_handlers[CallToolRequest](
            CallToolRequest(
                method="tools/call",
                params={"name": "get_version", "arguments": {"instance": "de"}},
            )
        )
        data = json.loads(result.root.content[0].text)
        assert data["releaseName"] == "8.18.0"
        assert version_route.called


@pytest.mark.asyncio
async def test_call_tool_denies_inaccessible_instance(monkeypatch):
    # Country user pinned to "us" must not reach "de".
    monkeypatch.setenv("ARIAOPS_INSTANCES", TWO_INSTANCES)
    monkeypatch.setenv("ARIAOPS_DEFAULT_ROLE", "country")
    monkeypatch.setenv("ARIAOPS_DEFAULT_COUNTRY", "US")
    from ariaops_mcp.config import clear_settings_cache

    clear_settings_cache()
    server = create_server()
    result = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params={"name": "get_version", "arguments": {"instance": "de"}},
        )
    )
    data = json.loads(result.root.content[0].text)
    assert data["error"] == "Access denied"


@pytest.mark.asyncio
async def test_call_tool_ops_requires_instance_when_multiple(monkeypatch):
    monkeypatch.setenv("ARIAOPS_INSTANCES", TWO_INSTANCES)
    monkeypatch.setenv("ARIAOPS_DEFAULT_ROLE", "ops")
    from ariaops_mcp.config import clear_settings_cache

    clear_settings_cache()
    server = create_server()
    result = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params={"name": "get_version", "arguments": {}},
        )
    )
    data = json.loads(result.root.content[0].text)
    assert data["error"] == "Access denied"
    assert "specify the 'instance'" in data["detail"]
