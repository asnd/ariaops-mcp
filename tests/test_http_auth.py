"""Tests for HTTP transport OAuth 2.x authentication support."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import jwt
from starlette.testclient import TestClient

from ariaops_mcp.__main__ import create_http_app
from ariaops_mcp.client import reset_client_override, set_client_override
from ariaops_mcp.config import Settings


def _build_settings(**overrides: str | bool | list[str]) -> Settings:
    return Settings.model_validate(
        {
            "ARIAOPS_HOST": "vrops.test.local",
            "ARIAOPS_USERNAME": "testuser",
            "ARIAOPS_PASSWORD": "testpass",
            "ARIAOPS_VERIFY_SSL": False,
            "ARIAOPS_TRANSPORT": "http",
            "ARIAOPS_HTTP_OAUTH_ENABLED": False,
            "ARIAOPS_PORT": 8080,
            **overrides,
        }
    )


def _build_token(
    *,
    secret: str = "0123456789abcdef0123456789abcdef",
    issuer: str = "https://issuer.example.com/",
    audience: str = "https://mcp.example.com/",
    scope: str = "mcp:read",
    expires_delta: timedelta = timedelta(minutes=5),
    extra_claims: dict[str, object] | None = None,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": issuer,
        "aud": audience,
        "sub": "client-123",
        "scope": scope,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, secret, algorithm="HS256")


class _FakeSessionManager:
    def __init__(self) -> None:
        self.call_count = 0

    @asynccontextmanager
    async def run(self):
        yield

    async def handle_request(self, scope, receive, send) -> None:
        self.call_count += 1
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"ok": true}'})


class _HealthyClient:
    class _CircuitBreaker:
        class _State:
            value = "closed"

        state = _State()

    circuit_breaker = _CircuitBreaker()

    async def get(self, _path: str):
        return {"releaseName": "8.18.0"}

    async def close(self) -> None:
        return None


def test_http_transport_without_oauth_allows_requests():
    session_manager = _FakeSessionManager()
    app = create_http_app(
        server=object(),
        settings=_build_settings(),
        session_manager=session_manager,
    )

    with TestClient(app) as client:
        response = client.post("/", json={"jsonrpc": "2.0"})

    assert response.status_code == 200
    assert session_manager.call_count == 1


def test_http_transport_with_valid_oauth_token_allows_requests():
    session_manager = _FakeSessionManager()
    settings = _build_settings(
        ARIAOPS_HTTP_OAUTH_ENABLED=True,
        ARIAOPS_HTTP_OAUTH_ISSUER_URL="https://issuer.example.com",
        ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL="https://mcp.example.com",
        ARIAOPS_HTTP_OAUTH_JWT_KEY="0123456789abcdef0123456789abcdef",
        ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES=["mcp:read"],
    )
    app = create_http_app(server=object(), settings=settings, session_manager=session_manager)

    with TestClient(app) as client:
        response = client.post("/", headers={"Authorization": f"Bearer {_build_token()}"}, json={"jsonrpc": "2.0"})

    assert response.status_code == 200
    assert session_manager.call_count == 1


def test_http_transport_rejects_missing_oauth_token():
    settings = _build_settings(
        ARIAOPS_HTTP_OAUTH_ENABLED=True,
        ARIAOPS_HTTP_OAUTH_ISSUER_URL="https://issuer.example.com",
        ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL="https://mcp.example.com",
        ARIAOPS_HTTP_OAUTH_JWT_KEY="0123456789abcdef0123456789abcdef",
        ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES=["mcp:read"],
    )
    app = create_http_app(server=object(), settings=settings, session_manager=_FakeSessionManager())

    with TestClient(app) as client:
        response = client.post("/", json={"jsonrpc": "2.0"})

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"
    assert "resource_metadata" in response.headers["www-authenticate"]


def test_http_transport_rejects_expired_oauth_token():
    settings = _build_settings(
        ARIAOPS_HTTP_OAUTH_ENABLED=True,
        ARIAOPS_HTTP_OAUTH_ISSUER_URL="https://issuer.example.com",
        ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL="https://mcp.example.com",
        ARIAOPS_HTTP_OAUTH_JWT_KEY="0123456789abcdef0123456789abcdef",
    )
    app = create_http_app(server=object(), settings=settings, session_manager=_FakeSessionManager())
    token = _build_token(expires_delta=timedelta(minutes=-5))

    with TestClient(app) as client:
        response = client.post("/", headers={"Authorization": f"Bearer {token}"}, json={"jsonrpc": "2.0"})

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


def test_http_transport_rejects_wrong_issuer_token():
    settings = _build_settings(
        ARIAOPS_HTTP_OAUTH_ENABLED=True,
        ARIAOPS_HTTP_OAUTH_ISSUER_URL="https://issuer.example.com",
        ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL="https://mcp.example.com",
        ARIAOPS_HTTP_OAUTH_JWT_KEY="0123456789abcdef0123456789abcdef",
    )
    app = create_http_app(server=object(), settings=settings, session_manager=_FakeSessionManager())
    token = _build_token(issuer="https://other-issuer.example.com")

    with TestClient(app) as client:
        response = client.post("/", headers={"Authorization": f"Bearer {token}"}, json={"jsonrpc": "2.0"})

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


def test_http_transport_rejects_malformed_token():
    settings = _build_settings(
        ARIAOPS_HTTP_OAUTH_ENABLED=True,
        ARIAOPS_HTTP_OAUTH_ISSUER_URL="https://issuer.example.com",
        ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL="https://mcp.example.com",
        ARIAOPS_HTTP_OAUTH_JWT_KEY="0123456789abcdef0123456789abcdef",
    )
    app = create_http_app(server=object(), settings=settings, session_manager=_FakeSessionManager())

    with TestClient(app) as client:
        response = client.post("/", headers={"Authorization": "Bearer not-a-jwt"}, json={"jsonrpc": "2.0"})

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


def test_http_transport_rejects_insufficient_scope():
    settings = _build_settings(
        ARIAOPS_HTTP_OAUTH_ENABLED=True,
        ARIAOPS_HTTP_OAUTH_ISSUER_URL="https://issuer.example.com",
        ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL="https://mcp.example.com",
        ARIAOPS_HTTP_OAUTH_JWT_KEY="0123456789abcdef0123456789abcdef",
        ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES=["mcp:write"],
    )
    app = create_http_app(server=object(), settings=settings, session_manager=_FakeSessionManager())
    auth_header = {"Authorization": f"Bearer {_build_token(scope='mcp:read')}"}

    with TestClient(app) as client:
        response = client.post("/", headers=auth_header, json={"jsonrpc": "2.0"})

    assert response.status_code == 403
    assert response.json()["error"] == "insufficient_scope"


def test_http_transport_exposes_protected_resource_metadata():
    settings = _build_settings(
        ARIAOPS_HTTP_OAUTH_ENABLED=True,
        ARIAOPS_HTTP_OAUTH_ISSUER_URL="https://issuer.example.com",
        ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL="https://mcp.example.com",
        ARIAOPS_HTTP_OAUTH_JWT_KEY="0123456789abcdef0123456789abcdef",
        ARIAOPS_HTTP_OAUTH_REQUIRED_SCOPES=["mcp:read"],
    )
    app = create_http_app(server=object(), settings=settings, session_manager=_FakeSessionManager())

    with TestClient(app) as client:
        response = client.get("/.well-known/oauth-protected-resource")

    assert response.status_code == 200
    payload = response.json()
    assert payload["resource"] == "https://mcp.example.com/"
    assert payload["authorization_servers"] == ["https://issuer.example.com/"]
    assert payload["scopes_supported"] == ["mcp:read"]


def test_health_endpoint_remains_unprotected_with_oauth():
    settings = _build_settings(
        ARIAOPS_HTTP_OAUTH_ENABLED=True,
        ARIAOPS_HTTP_OAUTH_ISSUER_URL="https://issuer.example.com",
        ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL="https://mcp.example.com",
        ARIAOPS_HTTP_OAUTH_JWT_KEY="0123456789abcdef0123456789abcdef",
    )
    app = create_http_app(server=object(), settings=settings, session_manager=_FakeSessionManager())
    token = set_client_override(_HealthyClient())

    try:
        with TestClient(app) as client:
            response = client.get("/health")
    finally:
        reset_client_override(token)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
