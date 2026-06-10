"""Tests for dual authentication (ARIAOPS_HTTP_AUTH_MODE=both)."""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import jwt
import pytest
from starlette.testclient import TestClient

from ariaops_mcp.__main__ import create_http_app
from ariaops_mcp.config import Settings
from ariaops_mcp.ldap_auth import CompositeAuthBackend, LDAPAuthenticator

_HS_SECRET = "0123456789abcdef0123456789abcdef"
_ISSUER = "https://issuer.example.com"
_RESOURCE = "https://mcp.example.com"


def _build_settings(**extra: Any) -> Settings:
    return Settings.model_validate(
        {
            "ARIAOPS_HOST": "vrops.test.local",
            "ARIAOPS_USERNAME": "svc",
            "ARIAOPS_PASSWORD": "pass",
            "ARIAOPS_VERIFY_SSL": False,
            "ARIAOPS_TRANSPORT": "http",
            "ARIAOPS_HTTP_AUTH_MODE": "both",
            "ARIAOPS_HTTP_OAUTH_ISSUER_URL": _ISSUER,
            "ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL": _RESOURCE,
            "ARIAOPS_HTTP_OAUTH_JWT_KEY": _HS_SECRET,
            "ARIAOPS_LDAP_SERVER_URI": "ldaps://dc.corp.example.com:636",
            "ARIAOPS_LDAP_USER_DN_TEMPLATE": "{username}@corp.example.com",
            "ARIAOPS_LDAP_USER_SEARCH_BASE": "dc=corp,dc=example,dc=com",
            "ARIAOPS_LDAP_VERIFY_TLS": False,
            **extra,
        }
    )


def _bearer_token(scope: str = "mcp:read", **extra_claims: Any) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": _ISSUER,
        "aud": _RESOURCE,
        "sub": "client-123",
        "scope": scope,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        **extra_claims,
    }
    return jwt.encode(claims, _HS_SECRET, algorithm="HS256")


def _basic_header(username: str, password: str) -> str:
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {credentials}"


class _FakeSessionManager:
    @asynccontextmanager
    async def run(self):
        yield

    async def handle_request(self, scope, receive, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"ok": true}'})


def _make_app(settings: Settings, *, ldap_groups: list[str] | None = None):
    """Build the app with the LDAP bind stubbed (None = bind failure)."""

    def _fake_bind(self: Any, username: str, password: str) -> list[str] | None:
        return ldap_groups

    patcher = patch.object(LDAPAuthenticator, "_sync_bind_and_get_groups", _fake_bind)
    patcher.start()
    try:
        return create_http_app(
            server=object(), settings=settings, session_manager=_FakeSessionManager()
        ), patcher
    except Exception:
        patcher.stop()
        raise


def test_both_mode_accepts_bearer_token():
    app, patcher = _make_app(_build_settings(), ldap_groups=None)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/",
                headers={"Authorization": f"Bearer {_bearer_token()}"},
                json={"jsonrpc": "2.0"},
            )
    finally:
        patcher.stop()
    assert response.status_code == 200


def test_both_mode_accepts_basic_credentials():
    app, patcher = _make_app(
        _build_settings(), ldap_groups=["CN=vrops-ops,DC=corp,DC=example,DC=com"]
    )
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/",
                headers={"Authorization": _basic_header("alice", "secret")},
                json={"jsonrpc": "2.0"},
            )
    finally:
        patcher.stop()
    assert response.status_code == 200


def test_both_mode_no_header_401_with_both_challenges():
    app, patcher = _make_app(_build_settings(), ldap_groups=None)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/", json={"jsonrpc": "2.0"})
    finally:
        patcher.stop()
    assert response.status_code == 401
    challenges = response.headers.get_list("www-authenticate")
    assert any(c.startswith("Bearer") for c in challenges)
    assert any(c.startswith("Basic") for c in challenges)


def test_both_mode_bad_basic_credentials_401():
    app, patcher = _make_app(_build_settings(), ldap_groups=None)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/",
                headers={"Authorization": _basic_header("alice", "wrong")},
                json={"jsonrpc": "2.0"},
            )
    finally:
        patcher.stop()
    assert response.status_code == 401


def test_both_mode_bad_bearer_token_401():
    app, patcher = _make_app(_build_settings(), ldap_groups=None)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/",
                headers={"Authorization": "Bearer not-a-jwt"},
                json={"jsonrpc": "2.0"},
            )
    finally:
        patcher.stop()
    assert response.status_code == 401


def test_both_mode_scope_enforced_for_bearer_only():
    """Required scopes 403 an under-scoped bearer token but exempt LDAP users."""
    settings = _build_settings(ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES=["mcp:write"])
    app, patcher = _make_app(settings, ldap_groups=["CN=vrops-ops,DC=corp,DC=example,DC=com"])
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            bearer_response = client.post(
                "/",
                headers={"Authorization": f"Bearer {_bearer_token(scope='mcp:read')}"},
                json={"jsonrpc": "2.0"},
            )
            basic_response = client.post(
                "/",
                headers={"Authorization": _basic_header("alice", "secret")},
                json={"jsonrpc": "2.0"},
            )
    finally:
        patcher.stop()
    assert bearer_response.status_code == 403
    assert bearer_response.json()["error"] == "insufficient_scope"
    assert basic_response.status_code == 200


def test_both_mode_serves_protected_resource_metadata():
    app, patcher = _make_app(_build_settings(), ldap_groups=None)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/.well-known/oauth-protected-resource")
    finally:
        patcher.stop()
    assert response.status_code == 200
    assert response.json()["authorization_servers"] == [f"{_ISSUER}/"]


def test_both_mode_health_endpoint_open():
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

    app, patcher = _make_app(_build_settings(), ldap_groups=None)
    token = set_client_override(_FakeClient())
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/health")
    finally:
        reset_client_override(token)
        patcher.stop()
    assert response.status_code == 200


# ── CompositeAuthBackend unit tests ───────────────────────────────────────────


class _RecordingBackend:
    def __init__(self) -> None:
        self.called = False

    async def authenticate(self, conn):
        self.called = True
        return None


@pytest.mark.asyncio
async def test_composite_dispatches_bearer():
    bearer, basic = _RecordingBackend(), _RecordingBackend()
    composite = CompositeAuthBackend(bearer, basic)
    conn = MagicMock()
    conn.headers = {"Authorization": "Bearer abc"}
    await composite.authenticate(conn)
    assert bearer.called and not basic.called


@pytest.mark.asyncio
async def test_composite_dispatches_basic():
    bearer, basic = _RecordingBackend(), _RecordingBackend()
    composite = CompositeAuthBackend(bearer, basic)
    conn = MagicMock()
    conn.headers = {"Authorization": "Basic abc"}
    await composite.authenticate(conn)
    assert basic.called and not bearer.called


@pytest.mark.asyncio
async def test_composite_unknown_scheme_returns_none():
    bearer, basic = _RecordingBackend(), _RecordingBackend()
    composite = CompositeAuthBackend(bearer, basic)
    conn = MagicMock()
    conn.headers = {"Authorization": "Digest abc"}
    assert await composite.authenticate(conn) is None
    assert not bearer.called and not basic.called


@pytest.mark.asyncio
async def test_composite_missing_header_returns_none():
    composite = CompositeAuthBackend(_RecordingBackend(), _RecordingBackend())
    conn = MagicMock()
    conn.headers = {}
    assert await composite.authenticate(conn) is None


def test_config_oauth_enabled_with_both_mode_allowed():
    settings = _build_settings(ARIAOPS_HTTP_OAUTH_ENABLED=True)
    assert settings.effective_auth_mode == "both"
