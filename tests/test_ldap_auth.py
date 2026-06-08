"""Tests for LDAP/AD authentication support."""

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
    LDAPAuthenticator,
    _extract_cn,
    map_groups_to_scopes,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


# Base dict that explicitly overrides OAuth fields so that any values already
# present in os.environ (e.g. from a sourced .env) don't leak into LDAP tests.
_BASE = {
    "ARIAOPS_HOST": "vrops.test.local",
    "ARIAOPS_USERNAME": "svc",
    "ARIAOPS_PASSWORD": "pass",
    "ARIAOPS_VERIFY_SSL": False,
    "ARIAOPS_TRANSPORT": "http",
    "ARIAOPS_HTTP_OAUTH_ENABLED": False,
    "ARIAOPS_HTTP_OAUTH_ISSUER_URL": None,
    "ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL": None,
    "ARIAOPS_HTTP_OAUTH_JWT_KEY": None,
    "ARIAOPS_HTTP_OAUTH_JWKS_URL": None,
    "ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES": [],
    "ARIAOPS_TRUST_ENV": True,
}


def _build_settings(**overrides: Any) -> Settings:
    return Settings.model_validate({**_BASE, **overrides})


def _basic_header(username: str, password: str) -> str:
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {credentials}"


def _make_authenticator(
    bind_succeeds: bool = True,
    groups: list[str] | None = None,
    group_scope_map: dict[str, list[str]] | None = None,
    cache_ttl: int = 300,
) -> LDAPAuthenticator:
    """Create an LDAPAuthenticator with a mocked LDAP connection."""
    auth = LDAPAuthenticator(
        server_uri="ldaps://dc.corp.example.com:636",
        user_dn_template="{username}@corp.example.com",
        user_search_base="dc=corp,dc=example,dc=com",
        group_scope_map=group_scope_map or {"vrops-ro": ["ariaops:prod:read"]},
        verify_tls=False,  # skip real TLS in tests
        cache_ttl=cache_ttl,
    )

    def _fake_sync(username: str, password: str) -> list[str] | None:
        if not bind_succeeds:
            return None
        return groups if groups is not None else ["CN=vrops-ro,OU=Groups,DC=corp,DC=example,DC=com"]

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
    assert _extract_cn("CN=vrops-ro,OU=Groups,DC=corp,DC=com") == "vrops-ro"


def test_extract_cn_plain_name():
    assert _extract_cn("vrops-ro") == "vrops-ro"


def test_extract_cn_case_insensitive_prefix():
    assert _extract_cn("cn=My Group,dc=example,dc=com") == "My Group"


# ── map_groups_to_scopes ──────────────────────────────────────────────────────


def test_map_groups_exact_key_match():
    groups = ["vrops-ro"]
    scope_map = {"vrops-ro": ["ariaops:prod:read"]}
    assert map_groups_to_scopes(groups, scope_map) == {"ariaops:prod:read"}


def test_map_groups_cn_fallback_from_full_dn():
    groups = ["CN=vrops-ro,OU=Groups,DC=corp,DC=com"]
    scope_map = {"vrops-ro": ["ariaops:prod:read"]}
    assert map_groups_to_scopes(groups, scope_map) == {"ariaops:prod:read"}


def test_map_groups_cn_case_insensitive():
    groups = ["CN=VROPS-RO,DC=corp,DC=com"]
    scope_map = {"vrops-ro": ["ariaops:prod:read"]}
    assert map_groups_to_scopes(groups, scope_map) == {"ariaops:prod:read"}


def test_map_groups_multiple_groups_union():
    groups = ["vrops-ro", "vrops-prod-write"]
    scope_map = {
        "vrops-ro": ["ariaops:*:read"],
        "vrops-prod-write": ["ariaops:prod:write"],
    }
    assert map_groups_to_scopes(groups, scope_map) == {"ariaops:*:read", "ariaops:prod:write"}


def test_map_groups_no_matching_group():
    groups = ["CN=other-group,DC=corp,DC=com"]
    scope_map = {"vrops-ro": ["ariaops:prod:read"]}
    assert map_groups_to_scopes(groups, scope_map) == set()


def test_map_groups_empty_inputs():
    assert map_groups_to_scopes([], {}) == set()
    assert map_groups_to_scopes(["CN=g,DC=x"], {}) == set()


# ── LDAPAuthenticator ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_authenticator_success_returns_scopes():
    auth = _make_authenticator(
        bind_succeeds=True,
        groups=["CN=vrops-ro,OU=Groups,DC=corp,DC=com"],
        group_scope_map={"vrops-ro": ["ariaops:prod:read"]},
    )
    scopes = await auth.authenticate("alice", "secret")
    assert scopes == {"ariaops:prod:read"}


@pytest.mark.asyncio
async def test_authenticator_failed_bind_returns_none():
    auth = _make_authenticator(bind_succeeds=False)
    result = await auth.authenticate("alice", "wrong")
    assert result is None


@pytest.mark.asyncio
async def test_authenticator_cache_hit_skips_bind():
    call_count = 0

    def _fake_sync(username: str, password: str) -> list[str] | None:
        nonlocal call_count
        call_count += 1
        return ["CN=vrops-ro,DC=corp,DC=com"]

    auth = LDAPAuthenticator(
        server_uri="ldaps://dc.corp.example.com:636",
        user_dn_template="{username}@corp.example.com",
        user_search_base="dc=corp,dc=example,dc=com",
        group_scope_map={"vrops-ro": ["ariaops:prod:read"]},
        verify_tls=False,
        cache_ttl=60,
    )
    auth._sync_bind_and_get_groups = _fake_sync  # type: ignore[method-assign]

    await auth.authenticate("alice", "secret")
    await auth.authenticate("alice", "secret")
    assert call_count == 1  # second call served from cache


@pytest.mark.asyncio
async def test_authenticator_cache_miss_after_ttl_expiry():
    call_count = 0

    def _fake_sync(username: str, password: str) -> list[str] | None:
        nonlocal call_count
        call_count += 1
        return []

    auth = LDAPAuthenticator(
        server_uri="ldaps://dc.corp.example.com:636",
        user_dn_template="{username}@corp.example.com",
        user_search_base="dc=corp,dc=example,dc=com",
        group_scope_map={},
        verify_tls=False,
        cache_ttl=1,
    )
    auth._sync_bind_and_get_groups = _fake_sync  # type: ignore[method-assign]

    await auth.authenticate("alice", "secret")
    assert call_count == 1

    # Manually expire the cache entry
    key = auth._cache_key("alice", "secret")
    auth._cache[key] = (set(), time.time() - 1)

    await auth.authenticate("alice", "secret")
    assert call_count == 2


@pytest.mark.asyncio
async def test_authenticator_failed_bind_not_cached():
    call_count = 0

    def _fake_sync(username: str, password: str) -> list[str] | None:
        nonlocal call_count
        call_count += 1
        return None  # auth failure

    auth = LDAPAuthenticator(
        server_uri="ldaps://dc.corp.example.com:636",
        user_dn_template="{username}@corp.example.com",
        user_search_base="dc=corp,dc=example,dc=com",
        group_scope_map={},
        verify_tls=False,
        cache_ttl=300,
    )
    auth._sync_bind_and_get_groups = _fake_sync  # type: ignore[method-assign]

    await auth.authenticate("alice", "wrong")
    await auth.authenticate("alice", "wrong")
    assert call_count == 2  # each failure retried


# ── BasicLDAPAuthBackend ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_basic_backend_no_auth_header_returns_none():
    auth = _make_authenticator()
    backend = BasicLDAPAuthBackend(auth)

    conn = MagicMock()
    conn.headers = {}
    result = await backend.authenticate(conn)
    assert result is None


@pytest.mark.asyncio
async def test_basic_backend_bearer_header_ignored():
    auth = _make_authenticator()
    backend = BasicLDAPAuthBackend(auth)

    conn = MagicMock()
    conn.headers = {"Authorization": "Bearer some-token"}
    result = await backend.authenticate(conn)
    assert result is None


@pytest.mark.asyncio
async def test_basic_backend_malformed_base64_returns_none():
    auth = _make_authenticator()
    backend = BasicLDAPAuthBackend(auth)

    conn = MagicMock()
    conn.headers = {"Authorization": "Basic !!!notbase64!!!"}
    result = await backend.authenticate(conn)
    assert result is None


@pytest.mark.asyncio
async def test_basic_backend_success_returns_authenticated_user():
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

    auth = _make_authenticator(
        bind_succeeds=True,
        groups=["CN=vrops-ro,DC=corp,DC=com"],
        group_scope_map={"vrops-ro": ["ariaops:prod:read"]},
    )
    backend = BasicLDAPAuthBackend(auth)

    conn = MagicMock()
    conn.headers = {"Authorization": _basic_header("alice", "secret")}
    result = await backend.authenticate(conn)

    assert result is not None
    credentials, user = result
    assert isinstance(user, AuthenticatedUser)
    assert user.access_token.client_id == "alice"
    assert "ariaops:prod:read" in user.access_token.scopes
    assert "ariaops:prod:read" in credentials.scopes


@pytest.mark.asyncio
async def test_basic_backend_wrong_password_returns_none():
    auth = _make_authenticator(bind_succeeds=False)
    backend = BasicLDAPAuthBackend(auth)

    conn = MagicMock()
    conn.headers = {"Authorization": _basic_header("alice", "wrong")}
    result = await backend.authenticate(conn)
    assert result is None


@pytest.mark.asyncio
async def test_basic_backend_empty_password_returns_none():
    auth = _make_authenticator()
    backend = BasicLDAPAuthBackend(auth)

    conn = MagicMock()
    # "alice:" encodes a username with empty password
    conn.headers = {"Authorization": _basic_header("alice", "")}
    result = await backend.authenticate(conn)
    assert result is None


# ── BasicRequireAuthMiddleware ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_require_middleware_passes_authenticated_with_scopes():
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken
    from starlette.authentication import AuthCredentials

    calls: list[str] = []

    async def inner_app(scope, receive, send):
        calls.append("inner")

    middleware = BasicRequireAuthMiddleware(inner_app, required_scopes=["ariaops:prod:read"])

    token = AccessToken(token="t", client_id="alice", scopes=["ariaops:prod:read"])
    scope = {
        "type": "http",
        "user": AuthenticatedUser(token),
        "auth": AuthCredentials(["ariaops:prod:read"]),
    }

    responses: list[dict] = []

    async def capture_send(event):
        responses.append(event)

    await middleware(scope, None, capture_send)
    assert calls == ["inner"]
    assert not responses


@pytest.mark.asyncio
async def test_require_middleware_rejects_unauthenticated():
    from starlette.authentication import UnauthenticatedUser

    async def inner_app(scope, receive, send):
        pass  # pragma: no cover

    middleware = BasicRequireAuthMiddleware(inner_app, required_scopes=["ariaops:prod:read"])
    scope = {"type": "http", "user": UnauthenticatedUser(), "auth": None}

    responses: list[dict] = []

    async def capture_send(event):
        responses.append(event)

    await middleware(scope, None, capture_send)

    start = responses[0]
    assert start["status"] == 401
    header_dict = dict(start["headers"])
    assert b"www-authenticate" in header_dict
    assert b"Basic" in header_dict[b"www-authenticate"]


@pytest.mark.asyncio
async def test_require_middleware_rejects_insufficient_scope():
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken
    from starlette.authentication import AuthCredentials

    async def inner_app(scope, receive, send):
        pass  # pragma: no cover

    middleware = BasicRequireAuthMiddleware(inner_app, required_scopes=["ariaops:prod:write"])

    token = AccessToken(token="t", client_id="alice", scopes=["ariaops:prod:read"])
    scope = {
        "type": "http",
        "user": AuthenticatedUser(token),
        "auth": AuthCredentials(["ariaops:prod:read"]),
    }

    responses: list[dict] = []

    async def capture_send(event):
        responses.append(event)

    await middleware(scope, None, capture_send)
    assert responses[0]["status"] == 403


# ── End-to-end HTTP app tests ─────────────────────────────────────────────────


def _build_ldap_settings(**extra: Any) -> Settings:
    return _build_settings(
        ARIAOPS_HTTP_AUTH_MODE="ldap",
        ARIAOPS_LDAP_SERVER_URI="ldaps://dc.corp.example.com:636",
        ARIAOPS_LDAP_USER_DN_TEMPLATE="{username}@corp.example.com",
        ARIAOPS_LDAP_USER_SEARCH_BASE="dc=corp,dc=example,dc=com",
        ARIAOPS_LDAP_GROUP_SCOPE_MAP='{"vrops-ro": ["ariaops:prod:read"]}',
        ARIAOPS_LDAP_VERIFY_TLS=False,
        **extra,
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
        return ["CN=vrops-ro,DC=corp,DC=com"]

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
        return None  # auth failure

    with patch.object(LDAPAuthenticator, "_sync_bind_and_get_groups", _fake_bind):
        app = create_http_app(server=object(), settings=settings, session_manager=_FakeSessionManager())

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/",
            headers={"Authorization": _basic_header("alice", "wrong")},
            json={"jsonrpc": "2.0"},
        )

    assert response.status_code == 401


def test_ldap_app_insufficient_scope_returns_403():
    settings = _build_ldap_settings(ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES="ariaops:prod:write")

    def _fake_bind(self: Any, username: str, password: str) -> list[str]:
        return ["CN=vrops-ro,DC=corp,DC=com"]  # only read scope

    with patch.object(LDAPAuthenticator, "_sync_bind_and_get_groups", _fake_bind):
        app = create_http_app(server=object(), settings=settings, session_manager=_FakeSessionManager())
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/",
                headers={"Authorization": _basic_header("alice", "secret")},
                json={"jsonrpc": "2.0"},
            )

    assert response.status_code == 403


def test_ldap_health_endpoint_unprotected():
    settings = _build_ldap_settings()

    with patch("ariaops_mcp.ldap_auth.LDAPAuthenticator._sync_bind_and_get_groups", return_value=None):
        app = create_http_app(server=object(), settings=settings, session_manager=_FakeSessionManager())

    from ariaops_mcp.client import reset_client_override, set_client_override

    class _FakeClient:
        class _CB:
            class _State:
                value = "closed"
            state = _State()
        circuit_breaker = _CB()
        async def get(self, _path: str): return {}
        async def close(self) -> None: return None

    token = set_client_override(_FakeClient())
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/health")
    finally:
        reset_client_override(token)

    assert response.status_code == 200


# ── Config validation ─────────────────────────────────────────────────────────


def test_config_ldap_mode_requires_transport_http():
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
                "ARIAOPS_LDAP_GROUP_SCOPE_MAP": '{"g": ["ariaops:prod:read"]}',
            }
        )


def test_config_ldap_mode_requires_server_uri():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="ARIAOPS_LDAP_SERVER_URI"):
        Settings.model_validate(
            {
                **_BASE,
                "ARIAOPS_HTTP_AUTH_MODE": "ldap",
                "ARIAOPS_LDAP_USER_DN_TEMPLATE": "{username}@corp.example.com",
                "ARIAOPS_LDAP_USER_SEARCH_BASE": "dc=corp,dc=com",
                "ARIAOPS_LDAP_GROUP_SCOPE_MAP": '{"g": ["ariaops:prod:read"]}',
            }
        )


def test_config_ldap_mode_rejects_non_ldaps_with_verify_tls():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="ldaps://"):
        Settings.model_validate(
            {
                **_BASE,
                "ARIAOPS_HTTP_AUTH_MODE": "ldap",
                "ARIAOPS_LDAP_SERVER_URI": "ldap://dc.corp.example.com",  # plain LDAP
                "ARIAOPS_LDAP_USER_DN_TEMPLATE": "{username}@corp.example.com",
                "ARIAOPS_LDAP_USER_SEARCH_BASE": "dc=corp,dc=com",
                "ARIAOPS_LDAP_GROUP_SCOPE_MAP": '{"g": ["ariaops:prod:read"]}',
                "ARIAOPS_LDAP_VERIFY_TLS": True,
            }
        )


def test_config_ldap_mode_rejects_empty_group_scope_map():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="ARIAOPS_LDAP_GROUP_SCOPE_MAP"):
        Settings.model_validate(
            {
                **_BASE,
                "ARIAOPS_HTTP_AUTH_MODE": "ldap",
                "ARIAOPS_LDAP_SERVER_URI": "ldaps://dc.corp.example.com",
                "ARIAOPS_LDAP_USER_DN_TEMPLATE": "{username}@corp.example.com",
                "ARIAOPS_LDAP_USER_SEARCH_BASE": "dc=corp,dc=com",
                "ARIAOPS_LDAP_GROUP_SCOPE_MAP": "{}",
            }
        )


def test_config_oauth_enabled_and_ldap_mode_conflict():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="conflict"):
        Settings.model_validate(
            {
                **_BASE,
                "ARIAOPS_HTTP_OAUTH_ENABLED": True,
                "ARIAOPS_HTTP_AUTH_MODE": "ldap",
            }
        )


def test_config_oauth_enabled_coerces_effective_auth_mode_to_oauth():
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


def test_config_ldap_happy_path():
    settings = _build_ldap_settings()
    assert settings.http_auth_mode == "ldap"
    assert settings.ldap_server_uri == "ldaps://dc.corp.example.com:636"
    assert settings.ldap_group_scope_map == {"vrops-ro": ["ariaops:prod:read"]}


def test_config_ldap_group_scope_map_json_parsing():
    settings = Settings.model_validate(
        {
            **_BASE,
            "ARIAOPS_HTTP_AUTH_MODE": "ldap",
            "ARIAOPS_LDAP_SERVER_URI": "ldaps://dc.corp.example.com:636",
            "ARIAOPS_LDAP_USER_DN_TEMPLATE": "{username}@corp.example.com",
            "ARIAOPS_LDAP_USER_SEARCH_BASE": "dc=corp,dc=com",
            "ARIAOPS_LDAP_GROUP_SCOPE_MAP": '{"vrops-ro": ["ariaops:prod:read"], "vrops-all": ["ariaops:*:read"]}',
            "ARIAOPS_LDAP_VERIFY_TLS": False,
        }
    )
    assert settings.ldap_group_scope_map == {
        "vrops-ro": ["ariaops:prod:read"],
        "vrops-all": ["ariaops:*:read"],
    }
