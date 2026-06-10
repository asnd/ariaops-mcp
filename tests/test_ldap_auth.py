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


# ── Search-then-bind ──────────────────────────────────────────────────────────


class _FakeEntry:
    def __init__(self, dn: str, member_of: list[str] | None = None) -> None:
        self.entry_dn = dn
        if member_of is not None:
            self.memberOf = MagicMock()
            self.memberOf.values = member_of
            self.memberOf.__bool__ = lambda _self: bool(member_of)


class _FakeConnection:
    """Captures search calls; entries are set per-search by the test."""

    def __init__(self, search_results: list[list[_FakeEntry]] | None = None) -> None:
        self.searches: list[dict[str, Any]] = []
        self._search_results = list(search_results or [])
        self.entries: list[_FakeEntry] = []
        self.unbound = False

    def search(self, search_base: str, search_filter: str, **kwargs: Any) -> None:
        self.searches.append({"base": search_base, "filter": search_filter, **kwargs})
        self.entries = self._search_results.pop(0) if self._search_results else []

    def unbind(self) -> None:
        self.unbound = True


def _make_search_bind_authenticator(**overrides: Any) -> LDAPAuthenticator:
    kwargs: dict[str, Any] = dict(
        server_uri="ldaps://dc.corp.example.com:636",
        bind_dn="cn=svc,dc=corp,dc=example,dc=com",
        bind_password="svc-secret",
        user_search_base="dc=corp,dc=example,dc=com",
        group_role_map={},
        role_claim=ROLE_CLAIM,
        country_claim=COUNTRY_CLAIM,
        instance_claim=INSTANCE_CLAIM,
        ops_role="ops",
        country_role="country",
        default_role="ops",
        verify_tls=False,
    )
    kwargs.update(overrides)
    return LDAPAuthenticator(**kwargs)


def test_search_then_bind_happy_path():
    auth = _make_search_bind_authenticator()
    user_entry = _FakeEntry(
        "cn=alice,ou=people,dc=corp,dc=example,dc=com",
        member_of=["CN=vrops-ops,DC=corp,DC=example,DC=com"],
    )
    service_conn = _FakeConnection(search_results=[[user_entry]])
    user_conn = _FakeConnection()
    connections = [service_conn, user_conn]
    bind_args: list[str] = []

    def fake_connect(user: str, password: str) -> _FakeConnection:
        bind_args.append(user)
        return connections.pop(0)

    auth._connect = fake_connect  # type: ignore[method-assign]
    groups = auth._sync_bind_and_get_groups("alice", "secret")

    assert groups == ["CN=vrops-ops,DC=corp,DC=example,DC=com"]
    # Service account binds first, then the found DN with the user's password.
    assert bind_args == [
        "cn=svc,dc=corp,dc=example,dc=com",
        "cn=alice,ou=people,dc=corp,dc=example,dc=com",
    ]
    assert service_conn.unbound and user_conn.unbound


def test_search_then_bind_escapes_filter_injection():
    """A crafted username must not be able to widen the search filter."""
    auth = _make_search_bind_authenticator()
    service_conn = _FakeConnection(search_results=[[]])
    auth._connect = lambda user, password: service_conn  # type: ignore[method-assign]

    auth._sync_bind_and_get_groups("*)(uid=*", "x")

    rendered = service_conn.searches[0]["filter"]
    assert "*)(uid=*" not in rendered
    assert "\\2a\\29\\28uid=\\2a" in rendered.lower()


def test_direct_bind_escapes_filter_injection():
    """Regression: the direct-bind group search must escape the username too."""
    auth = _make_authenticator()
    conn = _FakeConnection(search_results=[[]])
    auth._connect = lambda user, password: conn  # type: ignore[method-assign]
    # _make_authenticator stubs _sync_bind_and_get_groups; call the real one.
    LDAPAuthenticator._sync_direct_bind(auth, "*)(objectClass=*", "pw")

    rendered = conn.searches[0]["filter"]
    assert "*)(objectClass=*" not in rendered


def test_search_then_bind_user_not_found_denies():
    auth = _make_search_bind_authenticator()
    auth._connect = lambda user, password: _FakeConnection(search_results=[[]])  # type: ignore[method-assign]
    assert auth._sync_bind_and_get_groups("ghost", "pw") is None


def test_search_then_bind_ambiguous_match_denies():
    auth = _make_search_bind_authenticator()
    entries = [_FakeEntry("cn=a,dc=corp"), _FakeEntry("cn=b,dc=corp")]
    auth._connect = lambda user, password: _FakeConnection(search_results=[entries])  # type: ignore[method-assign]
    assert auth._sync_bind_and_get_groups("dup", "pw") is None


def test_search_then_bind_wrong_password_denies():
    from ldap3.core.exceptions import LDAPBindError

    auth = _make_search_bind_authenticator()
    user_entry = _FakeEntry("cn=alice,dc=corp", member_of=["CN=g,DC=corp"])
    service_conn = _FakeConnection(search_results=[[user_entry]])
    calls = {"n": 0}

    def fake_connect(user: str, password: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return service_conn
        raise LDAPBindError("invalid credentials")

    auth._connect = fake_connect  # type: ignore[method-assign]
    assert auth._sync_bind_and_get_groups("alice", "wrong") is None


def test_search_then_bind_service_account_failure_denies():
    from ldap3.core.exceptions import LDAPBindError

    auth = _make_search_bind_authenticator()

    def fake_connect(user: str, password: str):
        raise LDAPBindError("service account locked")

    auth._connect = fake_connect  # type: ignore[method-assign]
    assert auth._sync_bind_and_get_groups("alice", "secret") is None


def test_search_then_bind_group_search_fallback():
    """OpenLDAP without memberOf: groups come from a second search."""
    auth = _make_search_bind_authenticator(
        group_search_base="ou=groups,dc=corp,dc=example,dc=com",
    )
    user_entry = _FakeEntry("cn=alice,ou=people,dc=corp,dc=example,dc=com")
    group_entry = _FakeEntry("cn=vrops-se,ou=groups,dc=corp,dc=example,dc=com")
    service_conn = _FakeConnection(search_results=[[user_entry], [group_entry]])
    user_conn = _FakeConnection()
    connections = [service_conn, user_conn]
    auth._connect = lambda user, password: connections.pop(0)  # type: ignore[method-assign]

    groups = auth._sync_bind_and_get_groups("alice", "secret")

    assert groups == ["cn=vrops-se,ou=groups,dc=corp,dc=example,dc=com"]
    assert service_conn.searches[1]["base"] == "ou=groups,dc=corp,dc=example,dc=com"
    assert "cn=alice" in service_conn.searches[1]["filter"]


def test_starttls_uses_tls_before_bind(monkeypatch):
    import ldap3

    captured: dict[str, Any] = {}

    class _SpyConnection:
        def __init__(self, server, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(ldap3, "Connection", _SpyConnection)
    auth = _make_search_bind_authenticator(
        server_uri="ldap://dc.corp.example.com", starttls=True, receive_timeout=7
    )
    auth._server = object()  # skip real Server construction
    auth._connect("cn=svc,dc=corp", "pw")

    assert captured["auto_bind"] == ldap3.AUTO_BIND_TLS_BEFORE_BIND
    assert captured["receive_timeout"] == 7


def test_cache_key_is_salted_per_instance():
    a = _make_search_bind_authenticator()
    b = _make_search_bind_authenticator()
    assert a._cache_key("alice", "pw") != b._cache_key("alice", "pw")


# ── Config: search-then-bind / StartTLS / convenience vars ────────────────────


def _search_bind_config(**extra: Any) -> dict[str, Any]:
    return {
        **_BASE,
        "ARIAOPS_HTTP_AUTH_MODE": "ldap",
        "ARIAOPS_LDAP_SERVER_URI": "ldaps://dc.corp.example.com:636",
        "ARIAOPS_LDAP_BIND_DN": "cn=svc,dc=corp,dc=com",
        "ARIAOPS_LDAP_BIND_PASSWORD": "svc-secret",
        "ARIAOPS_LDAP_USER_SEARCH_BASE": "dc=corp,dc=com",
        **extra,
    }


def test_config_search_then_bind_happy_path():
    settings = Settings.model_validate(_search_bind_config())
    assert settings.ldap_bind_dn == "cn=svc,dc=corp,dc=com"
    assert settings.ldap_user_dn_template is None


def test_config_rejects_both_bind_modes():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="only one of"):
        Settings.model_validate(
            _search_bind_config(ARIAOPS_LDAP_USER_DN_TEMPLATE="{username}@corp.example.com")
        )


def test_config_rejects_neither_bind_mode():
    from pydantic import ValidationError

    config = _search_bind_config()
    del config["ARIAOPS_LDAP_BIND_DN"]
    del config["ARIAOPS_LDAP_BIND_PASSWORD"]
    with pytest.raises(ValidationError, match="either"):
        Settings.model_validate(config)


def test_config_bind_dn_requires_password():
    from pydantic import ValidationError

    config = _search_bind_config()
    del config["ARIAOPS_LDAP_BIND_PASSWORD"]
    with pytest.raises(ValidationError, match="BIND_PASSWORD"):
        Settings.model_validate(config)


def test_config_accepts_ldap_uri_with_starttls():
    settings = Settings.model_validate(
        _search_bind_config(
            ARIAOPS_LDAP_SERVER_URI="ldap://dc.corp.example.com",
            ARIAOPS_LDAP_STARTTLS=True,
        )
    )
    assert settings.ldap_starttls is True


def test_config_rejects_ldaps_with_starttls():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="STARTTLS"):
        Settings.model_validate(_search_bind_config(ARIAOPS_LDAP_STARTTLS=True))


def test_config_convenience_group_vars_merge_into_map():
    settings = Settings.model_validate(
        _search_bind_config(
            ARIAOPS_LDAP_OPS_GROUP="vrops-ops",
            ARIAOPS_LDAP_COUNTRY_GROUP_MAP='{"vrops-se": "SE", "vrops-de": "DE"}',
        )
    )
    assert settings.ldap_group_role_map == {
        "vrops-ops": {"role": "ops"},
        "vrops-se": {"role": "country", "country": "SE"},
        "vrops-de": {"role": "country", "country": "DE"},
    }


def test_config_explicit_group_map_wins_over_convenience_vars():
    settings = Settings.model_validate(
        _search_bind_config(
            ARIAOPS_LDAP_OPS_GROUP="vrops-ops",
            ARIAOPS_LDAP_GROUP_ROLE_MAP='{"vrops-ops": {"role": "country", "country": "SE"}}',
        )
    )
    assert settings.ldap_group_role_map["vrops-ops"] == {"role": "country", "country": "SE"}
