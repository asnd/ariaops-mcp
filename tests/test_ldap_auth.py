"""Tests for LDAP/AD authentication (role-claims integration)."""

from __future__ import annotations

import base64
import time
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from ariaops_mcp.__main__ import create_http_app
from ariaops_mcp.config import Settings
from ariaops_mcp.ldap_auth import (
    BasicLDAPAuthBackend,
    BasicRequireAuthMiddleware,
    ClaimsAccessToken,
    LDAPAuthenticator,
    _extract_cn,
    map_groups_to_claims,
)
from ariaops_mcp.principal import resolve_principal

# ── Helpers ───────────────────────────────────────────────────────────────────

# Explicitly override OAuth/instance fields so values already in os.environ
# (e.g. from a sourced .env) don't leak into these tests.
_BASE: dict[str, Any] = {
    "ARIAOPS_HOST": "vrops.test.local",
    "ARIAOPS_USERNAME": "svc",
    "ARIAOPS_PASSWORD": "pass",
    "ARIAOPS_VERIFY_SSL": False,
    "ARIAOPS_TRANSPORT": "http",
    "ARIAOPS_INSTANCES": None,
    "ARIAOPS_HTTP_OAUTH_ENABLED": False,
    "ARIAOPS_HTTP_OAUTH_ISSUER_URL": None,
    "ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL": None,
    "ARIAOPS_HTTP_OAUTH_JWT_KEY": None,
    "ARIAOPS_HTTP_OAUTH_JWKS_URL": None,
    "ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES": [],
}

# Claim names default to origin/main's principal defaults.
ROLE_CLAIM = "ariaops_role"
COUNTRY_CLAIM = "ariaops_country"
INSTANCE_CLAIM = "ariaops_instance"

_MAP_KWARGS = dict(
    role_claim=ROLE_CLAIM,
    country_claim=COUNTRY_CLAIM,
    instance_claim=INSTANCE_CLAIM,
    ops_role="ops",
    country_role="country",
)


def _basic_header(username: str, password: str) -> str:
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {credentials}"


def _make_authenticator(
    *,
    bind_succeeds: bool = True,
    groups: list[str] | None = None,
    group_role_map: dict[str, dict[str, str]] | None = None,
    cache_ttl: int = 300,
) -> LDAPAuthenticator:
    auth = LDAPAuthenticator(
        server_uri="ldaps://dc.corp.example.com:636",
        user_dn_template="{username}@corp.example.com",
        user_search_base="dc=corp,dc=example,dc=com",
        group_role_map=group_role_map if group_role_map is not None else {},
        role_claim=ROLE_CLAIM,
        country_claim=COUNTRY_CLAIM,
        instance_claim=INSTANCE_CLAIM,
        ops_role="ops",
        country_role="country",
        default_role="ops",
        verify_tls=False,
        cache_ttl=cache_ttl,
    )

    def _fake_sync(username: str, password: str) -> list[str] | None:
        if not bind_succeeds:
            return None
        return groups if groups is not None else []

    auth._sync_bind_and_get_groups = _fake_sync  # type: ignore[method-assign]
    return auth


class _FakeSessionManager:
    @asynccontextmanager
    async def run(self):
        yield

    async def handle_request(self, scope, receive, send) -> None:
        await send(
            {"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]}
        )
        await send({"type": "http.response.body", "body": b'{"ok": true}'})


# ── _extract_cn ───────────────────────────────────────────────────────────────


def test_extract_cn_from_full_dn():
    assert _extract_cn("CN=vrops-ops,OU=Groups,DC=corp,DC=com") == "vrops-ops"


def test_extract_cn_plain_name():
    assert _extract_cn("vrops-ops") == "vrops-ops"


def test_extract_cn_case_insensitive_prefix():
    assert _extract_cn("cn=My Group,dc=example,dc=com") == "My Group"


# ── map_groups_to_claims ──────────────────────────────────────────────────────


def test_map_ops_group_grants_ops_role():
    claims = map_groups_to_claims(
        ["CN=vrops-ops,DC=corp,DC=com"], {"vrops-ops": {"role": "ops"}}, **_MAP_KWARGS
    )
    assert claims == {ROLE_CLAIM: "ops"}


def test_map_country_group_sets_country_claim():
    claims = map_groups_to_claims(
        ["CN=vrops-se,DC=corp,DC=com"],
        {"vrops-se": {"role": "country", "country": "SE"}},
        **_MAP_KWARGS,
    )
    assert claims == {ROLE_CLAIM: "country", COUNTRY_CLAIM: "SE"}


def test_map_country_group_sets_instance_claim():
    claims = map_groups_to_claims(
        ["CN=vrops-de,DC=corp,DC=com"],
        {"vrops-de": {"role": "country", "instance": "de"}},
        **_MAP_KWARGS,
    )
    assert claims == {ROLE_CLAIM: "country", INSTANCE_CLAIM: "de"}


def test_map_ops_wins_over_country():
    groups = ["CN=vrops-se,DC=corp,DC=com", "CN=vrops-ops,DC=corp,DC=com"]
    scope_map = {
        "vrops-se": {"role": "country", "country": "SE"},
        "vrops-ops": {"role": "ops"},
    }
    claims = map_groups_to_claims(groups, scope_map, **_MAP_KWARGS)
    assert claims == {ROLE_CLAIM: "ops"}


def test_map_cn_match_from_full_dn_case_insensitive():
    claims = map_groups_to_claims(
        ["CN=VROPS-OPS,OU=x,DC=corp,DC=com"], {"vrops-ops": {"role": "ops"}}, **_MAP_KWARGS
    )
    assert claims == {ROLE_CLAIM: "ops"}


def test_map_no_match_returns_none():
    claims = map_groups_to_claims(
        ["CN=other,DC=corp,DC=com"], {"vrops-ops": {"role": "ops"}}, **_MAP_KWARGS
    )
    assert claims is None


def test_map_empty_groups_returns_none():
    assert map_groups_to_claims([], {"vrops-ops": {"role": "ops"}}, **_MAP_KWARGS) is None


# ── LDAPAuthenticator ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_authenticator_no_map_grants_default_role():
    auth = _make_authenticator(bind_succeeds=True, groups=["CN=whatever,DC=corp,DC=com"])
    claims = await auth.authenticate("alice", "secret")
    assert claims == {ROLE_CLAIM: "ops"}


@pytest.mark.asyncio
async def test_authenticator_mapped_country_group():
    auth = _make_authenticator(
        bind_succeeds=True,
        groups=["CN=vrops-se,DC=corp,DC=com"],
        group_role_map={"vrops-se": {"role": "country", "country": "SE"}},
    )
    claims = await auth.authenticate("alice", "secret")
    assert claims == {ROLE_CLAIM: "country", COUNTRY_CLAIM: "SE"}


@pytest.mark.asyncio
async def test_authenticator_failed_bind_returns_none():
    auth = _make_authenticator(bind_succeeds=False)
    assert await auth.authenticate("alice", "wrong") is None


@pytest.mark.asyncio
async def test_authenticator_bound_but_unmapped_returns_none():
    auth = _make_authenticator(
        bind_succeeds=True,
        groups=["CN=other,DC=corp,DC=com"],
        group_role_map={"vrops-ops": {"role": "ops"}},
    )
    assert await auth.authenticate("alice", "secret") is None


@pytest.mark.asyncio
async def test_authenticator_cache_hit_skips_bind():
    call_count = 0

    def _fake_sync(username: str, password: str) -> list[str]:
        nonlocal call_count
        call_count += 1
        return ["CN=vrops-ops,DC=corp,DC=com"]

    auth = _make_authenticator(group_role_map={"vrops-ops": {"role": "ops"}}, cache_ttl=60)
    auth._sync_bind_and_get_groups = _fake_sync  # type: ignore[method-assign]

    await auth.authenticate("alice", "secret")
    await auth.authenticate("alice", "secret")
    assert call_count == 1


@pytest.mark.asyncio
async def test_authenticator_failed_bind_not_cached():
    call_count = 0

    def _fake_sync(username: str, password: str) -> None:
        nonlocal call_count
        call_count += 1
        return None

    auth = _make_authenticator(cache_ttl=300)
    auth._sync_bind_and_get_groups = _fake_sync  # type: ignore[method-assign]

    await auth.authenticate("alice", "wrong")
    await auth.authenticate("alice", "wrong")
    assert call_count == 2


@pytest.mark.asyncio
async def test_authenticator_cache_expiry():
    call_count = 0

    def _fake_sync(username: str, password: str) -> list[str]:
        nonlocal call_count
        call_count += 1
        return ["CN=vrops-ops,DC=corp,DC=com"]

    auth = _make_authenticator(group_role_map={"vrops-ops": {"role": "ops"}}, cache_ttl=1)
    auth._sync_bind_and_get_groups = _fake_sync  # type: ignore[method-assign]

    await auth.authenticate("alice", "secret")
    key = auth._cache_key("alice", "secret")
    auth._cache[key] = ({ROLE_CLAIM: "ops"}, time.time() - 1)  # force-expire
    await auth.authenticate("alice", "secret")
    assert call_count == 2


def test_cache_key_is_keyed_per_authenticator():
    # HMAC under a per-process random key: identical credentials must not hash
    # to a value an attacker could precompute offline.
    auth_a = _make_authenticator()
    auth_b = _make_authenticator()
    assert auth_a._cache_key("alice", "secret") == auth_a._cache_key("alice", "secret")
    assert auth_a._cache_key("alice", "secret") != auth_b._cache_key("alice", "secret")


def test_set_cache_sweeps_expired_and_caps_size(monkeypatch):
    import ariaops_mcp.ldap_auth as ldap_auth_mod

    monkeypatch.setattr(ldap_auth_mod, "_CACHE_MAX_ENTRIES", 3)
    auth = _make_authenticator(cache_ttl=300)

    auth._cache["expired-1"] = ({ROLE_CLAIM: "ops"}, time.time() - 10)
    auth._cache["expired-2"] = ({ROLE_CLAIM: "ops"}, time.time() - 10)
    auth._set_cache("live-1", {ROLE_CLAIM: "ops"})
    auth._set_cache("live-2", {ROLE_CLAIM: "ops"})
    auth._set_cache("live-3", {ROLE_CLAIM: "ops"})
    # The cap triggered a sweep: expired entries are gone, live ones kept.
    assert "expired-1" not in auth._cache
    assert "expired-2" not in auth._cache
    # A further insert at the cap evicts the soonest-expiring live entry.
    auth._set_cache("live-4", {ROLE_CLAIM: "ops"})
    assert len(auth._cache) <= 3
    assert "live-4" in auth._cache


def test_sync_bind_escapes_filter_metacharacters():
    """A username with LDAP filter metacharacters must not widen the search."""
    captured: dict[str, str] = {}

    class _FakeConn:
        entries: list[Any] = []

        def search(self, search_base: str, search_filter: str, attributes: list[str]) -> None:
            captured["filter"] = search_filter

        def unbind(self) -> None:
            pass

    auth = LDAPAuthenticator(
        server_uri="ldaps://dc.corp.example.com:636",
        user_dn_template="{username}@corp.example.com",
        user_search_base="dc=corp,dc=example,dc=com",
        group_role_map={},
        role_claim=ROLE_CLAIM,
        country_claim=COUNTRY_CLAIM,
        instance_claim=INSTANCE_CLAIM,
        ops_role="ops",
        country_role="country",
        verify_tls=False,
    )

    with (
        patch.object(LDAPAuthenticator, "_get_server", return_value=object()),
        patch("ldap3.Connection", return_value=_FakeConn()),
    ):
        groups = auth._sync_bind_and_get_groups("evil*)(sAMAccountName=admin", "pw")

    assert groups == []
    sent = captured["filter"]
    assert "*)(" not in sent  # raw metacharacters never reach the directory
    assert r"evil\2a\29\28sAMAccountName=admin" in sent


# ── LDAP → principal contract ─────────────────────────────────────────────────


def _settings_with_instances() -> Settings:
    return Settings.model_validate(
        {
            **_BASE,
            "ARIAOPS_TRANSPORT": "stdio",
            "ARIAOPS_INSTANCES": (
                '[{"id":"se","host":"se.example.com","username":"u","password":"p","country":"SE"},'
                '{"id":"de","host":"de.example.com","username":"u","password":"p","country":"DE"}]'
            ),
            "ARIAOPS_HOST": None,
            "ARIAOPS_USERNAME": None,
            "ARIAOPS_PASSWORD": None,
        }
    )


def test_ldap_ops_claims_resolve_to_all_instances():
    settings = _settings_with_instances()
    claims = map_groups_to_claims(
        ["CN=vrops-ops,DC=corp,DC=com"], {"vrops-ops": {"role": "ops"}}, **_MAP_KWARGS
    )
    principal = resolve_principal(claims=claims, settings=settings)
    assert principal.role == "ops"
    assert principal.can_access("se")
    assert principal.can_access("de")


def test_ldap_country_claims_pin_single_instance():
    settings = _settings_with_instances()
    claims = map_groups_to_claims(
        ["CN=vrops-se,DC=corp,DC=com"],
        {"vrops-se": {"role": "country", "country": "SE"}},
        **_MAP_KWARGS,
    )
    principal = resolve_principal(claims=claims, settings=settings)
    assert principal.role == "country"
    assert principal.can_access("se")
    assert not principal.can_access("de")


# ── BasicLDAPAuthBackend ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backend_no_auth_header_returns_none():
    backend = BasicLDAPAuthBackend(_make_authenticator())
    conn = MagicMock()
    conn.headers = {}
    assert await backend.authenticate(conn) is None


@pytest.mark.asyncio
async def test_backend_bearer_header_ignored():
    backend = BasicLDAPAuthBackend(_make_authenticator())
    conn = MagicMock()
    conn.headers = {"Authorization": "Bearer xyz"}
    assert await backend.authenticate(conn) is None


@pytest.mark.asyncio
async def test_backend_malformed_base64_returns_none():
    backend = BasicLDAPAuthBackend(_make_authenticator())
    conn = MagicMock()
    conn.headers = {"Authorization": "Basic !!!notbase64!!!"}
    assert await backend.authenticate(conn) is None


@pytest.mark.asyncio
async def test_backend_success_returns_token_with_claims():
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

    auth = _make_authenticator(
        bind_succeeds=True,
        groups=["CN=vrops-se,DC=corp,DC=com"],
        group_role_map={"vrops-se": {"role": "country", "country": "SE"}},
    )
    backend = BasicLDAPAuthBackend(auth)
    conn = MagicMock()
    conn.headers = {"Authorization": _basic_header("alice", "secret")}
    result = await backend.authenticate(conn)

    assert result is not None
    _credentials, user = result
    assert isinstance(user, AuthenticatedUser)
    assert isinstance(user.access_token, ClaimsAccessToken)
    assert user.access_token.client_id == "alice"
    assert user.access_token.claims == {ROLE_CLAIM: "country", COUNTRY_CLAIM: "SE"}


@pytest.mark.asyncio
async def test_backend_wrong_password_returns_none():
    backend = BasicLDAPAuthBackend(_make_authenticator(bind_succeeds=False))
    conn = MagicMock()
    conn.headers = {"Authorization": _basic_header("alice", "wrong")}
    assert await backend.authenticate(conn) is None


@pytest.mark.asyncio
async def test_backend_empty_password_returns_none():
    backend = BasicLDAPAuthBackend(_make_authenticator())
    conn = MagicMock()
    conn.headers = {"Authorization": _basic_header("alice", "")}
    assert await backend.authenticate(conn) is None


# ── BasicRequireAuthMiddleware ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_middleware_passes_authenticated():
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from starlette.authentication import AuthCredentials

    calls: list[str] = []

    async def inner_app(scope, receive, send):
        calls.append("inner")

    middleware = BasicRequireAuthMiddleware(inner_app)
    token = ClaimsAccessToken(token="ldap", client_id="alice", scopes=[], claims={ROLE_CLAIM: "ops"})
    scope = {"type": "http", "user": AuthenticatedUser(token), "auth": AuthCredentials([])}

    responses: list[dict] = []

    async def capture_send(event):
        responses.append(event)

    await middleware(scope, None, capture_send)
    assert calls == ["inner"]
    assert not responses


@pytest.mark.asyncio
async def test_middleware_rejects_unauthenticated_with_basic_challenge():
    from starlette.authentication import UnauthenticatedUser

    async def inner_app(scope, receive, send):
        pass  # pragma: no cover

    middleware = BasicRequireAuthMiddleware(inner_app)
    scope = {"type": "http", "user": UnauthenticatedUser(), "auth": None}

    responses: list[dict] = []

    async def capture_send(event):
        responses.append(event)

    await middleware(scope, None, capture_send)
    start = responses[0]
    assert start["status"] == 401
    headers = dict(start["headers"])
    assert b"Basic" in headers[b"www-authenticate"]


# ── End-to-end HTTP app ───────────────────────────────────────────────────────


def _build_ldap_settings(**extra: Any) -> Settings:
    return Settings.model_validate(
        {
            **_BASE,
            "ARIAOPS_HTTP_AUTH_MODE": "ldap",
            "ARIAOPS_LDAP_SERVER_URI": "ldaps://dc.corp.example.com:636",
            "ARIAOPS_LDAP_USER_DN_TEMPLATE": "{username}@corp.example.com",
            "ARIAOPS_LDAP_USER_SEARCH_BASE": "dc=corp,dc=example,dc=com",
            "ARIAOPS_LDAP_VERIFY_TLS": False,
            **extra,
        }
    )


def test_ldap_app_no_credentials_returns_401():
    settings = _build_ldap_settings()
    with patch("ariaops_mcp.ldap_auth.LDAPAuthenticator._sync_bind_and_get_groups", return_value=None):
        app = create_http_app(server=object(), settings=settings, session_manager=_FakeSessionManager())
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/", json={"jsonrpc": "2.0"})
    assert response.status_code == 401
    assert "Basic" in response.headers.get("www-authenticate", "")


def test_ldap_app_valid_credentials_allows_request():
    settings = _build_ldap_settings()

    def _fake_bind(self: Any, username: str, password: str) -> list[str]:
        return ["CN=vrops-ops,DC=corp,DC=com"]

    with patch.object(LDAPAuthenticator, "_sync_bind_and_get_groups", _fake_bind):
        app = create_http_app(server=object(), settings=settings, session_manager=_FakeSessionManager())
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/",
                headers={"Authorization": _basic_header("alice", "secret")},
                json={"jsonrpc": "2.0"},
            )
    assert response.status_code == 200


def test_ldap_app_ignores_oauth_required_scopes():
    """Leftover ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES must not 403 LDAP requests."""
    settings = _build_ldap_settings(ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES="mcp:read,mcp:write")

    def _fake_bind(self: Any, username: str, password: str) -> list[str]:
        return ["CN=vrops-ops,DC=corp,DC=com"]

    with patch.object(LDAPAuthenticator, "_sync_bind_and_get_groups", _fake_bind):
        app = create_http_app(server=object(), settings=settings, session_manager=_FakeSessionManager())
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/",
                headers={"Authorization": _basic_header("alice", "secret")},
                json={"jsonrpc": "2.0"},
            )
    assert response.status_code == 200


def test_ldap_app_wrong_credentials_returns_401():
    settings = _build_ldap_settings()

    def _fake_bind(self: Any, username: str, password: str) -> None:
        return None

    with patch.object(LDAPAuthenticator, "_sync_bind_and_get_groups", _fake_bind):
        app = create_http_app(server=object(), settings=settings, session_manager=_FakeSessionManager())
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/",
                headers={"Authorization": _basic_header("alice", "wrong")},
                json={"jsonrpc": "2.0"},
            )
    assert response.status_code == 401


def test_ldap_health_endpoint_unprotected():
    settings = _build_ldap_settings()
    from ariaops_mcp.client import reset_client_override, set_client_override

    class _FakeClient:
        class _CB:
            class _State:
                value = "closed"

            state = _State()

        circuit_breaker = _CB()

        async def get(self, _path: str):
            return {}

        async def close(self) -> None:
            return None

    with patch("ariaops_mcp.ldap_auth.LDAPAuthenticator._sync_bind_and_get_groups", return_value=None):
        app = create_http_app(server=object(), settings=settings, session_manager=_FakeSessionManager())

    token = set_client_override(_FakeClient())
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/health")
    finally:
        reset_client_override(token)
    assert response.status_code == 200


# ── Config validation ─────────────────────────────────────────────────────────


def test_config_ldap_requires_transport_http():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="ARIAOPS_TRANSPORT=http"):
        Settings.model_validate(
            {
                **_BASE,
                "ARIAOPS_TRANSPORT": "stdio",
                "ARIAOPS_HTTP_AUTH_MODE": "ldap",
                "ARIAOPS_LDAP_SERVER_URI": "ldaps://dc.corp.example.com",
                "ARIAOPS_LDAP_USER_DN_TEMPLATE": "{username}@corp.example.com",
                "ARIAOPS_LDAP_USER_SEARCH_BASE": "dc=corp,dc=com",
            }
        )


def test_config_ldap_requires_server_uri():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="ARIAOPS_LDAP_SERVER_URI"):
        Settings.model_validate(
            {
                **_BASE,
                "ARIAOPS_HTTP_AUTH_MODE": "ldap",
                "ARIAOPS_LDAP_USER_DN_TEMPLATE": "{username}@corp.example.com",
                "ARIAOPS_LDAP_USER_SEARCH_BASE": "dc=corp,dc=com",
            }
        )


def test_config_ldap_rejects_non_ldaps_with_verify_tls():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="ldaps://"):
        Settings.model_validate(
            {
                **_BASE,
                "ARIAOPS_HTTP_AUTH_MODE": "ldap",
                "ARIAOPS_LDAP_SERVER_URI": "ldap://dc.corp.example.com",
                "ARIAOPS_LDAP_USER_DN_TEMPLATE": "{username}@corp.example.com",
                "ARIAOPS_LDAP_USER_SEARCH_BASE": "dc=corp,dc=com",
                "ARIAOPS_LDAP_VERIFY_TLS": True,
            }
        )


def test_config_oauth_enabled_and_ldap_mode_conflict():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="conflict"):
        Settings.model_validate(
            {**_BASE, "ARIAOPS_HTTP_OAUTH_ENABLED": True, "ARIAOPS_HTTP_AUTH_MODE": "ldap"}
        )


def test_config_effective_auth_mode_backward_compat():
    settings = Settings.model_validate(
        {
            **_BASE,
            "ARIAOPS_HTTP_OAUTH_ENABLED": True,
            "ARIAOPS_HTTP_OAUTH_ISSUER_URL": "https://issuer.example.com",
            "ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL": "https://mcp.example.com",
            "ARIAOPS_HTTP_OAUTH_JWT_KEY": "0123456789abcdef0123456789abcdef",
        }
    )
    assert settings.effective_auth_mode == "oauth"


def test_config_group_map_invalid_json_friendly_error():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="must be valid JSON"):
        _build_ldap_settings(ARIAOPS_LDAP_GROUP_ROLE_MAP="{not json")


def test_config_ldap_happy_path_and_group_map_parsing():
    settings = _build_ldap_settings(
        ARIAOPS_LDAP_GROUP_ROLE_MAP='{"vrops-ops": {"role": "ops"}, "vrops-se": {"role": "country", "country": "SE"}}'
    )
    assert settings.effective_auth_mode == "ldap"
    assert settings.ldap_server_uri == "ldaps://dc.corp.example.com:636"
    assert settings.ldap_group_role_map == {
        "vrops-ops": {"role": "ops"},
        "vrops-se": {"role": "country", "country": "SE"},
    }
