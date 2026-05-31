"""Tests for HTTP transport OAuth 2.x authentication support."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from ariaops_mcp.__main__ import create_http_app
from ariaops_mcp.client import reset_client_override, set_client_override
from ariaops_mcp.config import Settings
from ariaops_mcp.http_auth import JWTTokenVerifier


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


# ---------------------------------------------------------------------------
# JWTTokenVerifier unit tests — direct (no Starlette layer)
# ---------------------------------------------------------------------------

_HS_SECRET = "0123456789abcdef0123456789abcdef"
_VERIFIER_SETTINGS = dict(
    ARIAOPS_HTTP_OAUTH_ENABLED=True,
    ARIAOPS_HTTP_OAUTH_ISSUER_URL="https://issuer.example.com",
    ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL="https://mcp.example.com",
    ARIAOPS_HTTP_OAUTH_JWT_KEY=_HS_SECRET,
)


def _verifier(**overrides: Any) -> JWTTokenVerifier:
    settings = _build_settings(**{**_VERIFIER_SETTINGS, **overrides})
    return JWTTokenVerifier(settings)


@pytest.mark.asyncio
async def test_verifier_rejects_alg_none_token():
    """`alg: none` MUST be rejected even when the token is otherwise valid."""
    now = datetime.now(UTC)
    claims = {
        "iss": "https://issuer.example.com",
        "aud": "https://mcp.example.com",
        "sub": "client-123",
        "scope": "mcp:read",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    none_token = jwt.encode(claims, key="", algorithm="none")
    assert await _verifier().verify_token(none_token) is None


@pytest.mark.asyncio
async def test_verifier_rejects_algorithm_confusion():
    """Token signed with HS512 must be rejected when only HS256 is allowed."""
    now = datetime.now(UTC)
    claims = {
        "iss": "https://issuer.example.com",
        "aud": "https://mcp.example.com",
        "sub": "client-123",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    hs512_token = jwt.encode(claims, _HS_SECRET, algorithm="HS512")
    # Default config only allows HS256 → the HS512 token must be rejected.
    assert await _verifier().verify_token(hs512_token) is None


@pytest.mark.asyncio
async def test_verifier_rejects_empty_token():
    assert await _verifier().verify_token("") is None


@pytest.mark.asyncio
async def test_verifier_rejects_token_without_client_identity():
    """A token with no client_id/azp/appid/sub claim must be rejected."""
    verifier = _verifier()
    now = datetime.now(UTC)
    claims = {
        "iss": "https://issuer.example.com",
        "aud": "https://mcp.example.com",
        # no sub / client_id / azp / appid
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    token = jwt.encode(claims, _HS_SECRET, algorithm="HS256")
    assert await verifier.verify_token(token) is None


@pytest.mark.asyncio
async def test_verifier_accepts_azp_as_client_identity():
    """Keycloak emits `azp` (authorized party) — that should satisfy client identity.

    Build the claims directly so we can omit `sub` (PyJWT 2.12 rejects sub=None).
    """
    verifier = _verifier()
    now = datetime.now(UTC)
    claims = {
        "iss": "https://issuer.example.com",
        "aud": "https://mcp.example.com",
        "azp": "mcp-client",
        "scope": "mcp:read",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    token = jwt.encode(claims, _HS_SECRET, algorithm="HS256")
    access = await verifier.verify_token(token)
    assert access is not None
    assert access.client_id == "mcp-client"


@pytest.mark.asyncio
async def test_verifier_accepts_audience_as_list_with_match():
    verifier = _verifier()
    token = _build_token(
        extra_claims={"aud": ["other-aud", "https://mcp.example.com"]}
    )
    access = await verifier.verify_token(token)
    assert access is not None


@pytest.mark.asyncio
async def test_verifier_rejects_audience_as_list_no_match():
    verifier = _verifier()
    token = _build_token(extra_claims={"aud": ["nope-1", "nope-2"]})
    assert await verifier.verify_token(token) is None


@pytest.mark.asyncio
async def test_verifier_accepts_scope_as_json_list():
    """Some IdPs emit `scope` as a JSON list rather than a space-separated string."""
    verifier = _verifier()
    token = _build_token(extra_claims={"scope": ["mcp:read", "mcp:write"]})
    access = await verifier.verify_token(token)
    assert access is not None
    assert sorted(access.scopes) == ["mcp:read", "mcp:write"]


@pytest.mark.asyncio
async def test_verifier_accepts_token_within_clock_skew():
    """Default leeway 30s should let a token with exp slightly in the past pass."""
    verifier = _verifier()
    token = _build_token(expires_delta=timedelta(seconds=-10))
    access = await verifier.verify_token(token)
    assert access is not None


@pytest.mark.asyncio
async def test_verifier_audience_override_independent_of_resource_url():
    """When ARIAOPS_HTTP_OAUTH_AUDIENCE is set, that wins over RESOURCE_SERVER_URL.

    This is the typical Keycloak setup — `aud` is the OIDC client ID.
    """
    verifier = _verifier(ARIAOPS_HTTP_OAUTH_AUDIENCE="mcp-client")
    token = _build_token(extra_claims={"aud": "mcp-client"})
    access = await verifier.verify_token(token)
    assert access is not None
    assert access.resource == "mcp-client"


# ---------------------------------------------------------------------------
# Keycloak-shaped JWKS / RS256 path
# ---------------------------------------------------------------------------


def _generate_rsa_keypair() -> tuple[Any, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()

    def _b64u_uint(n: int) -> str:
        import base64

        length = (n.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode("ascii")

    jwk = {
        "kty": "RSA",
        "kid": "test-kid-1",
        "alg": "RS256",
        "use": "sig",
        "n": _b64u_uint(public_numbers.n),
        "e": _b64u_uint(public_numbers.e),
    }
    return private_key, jwk


def _sign_rs256(private_key: Any, claims: dict[str, Any], kid: str = "test-kid-1") -> str:
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": kid})


@pytest.mark.asyncio
async def test_verifier_jwks_path_keycloak_shape(monkeypatch):
    """End-to-end JWKS path: Keycloak-style RS256 token verified against JWKS."""
    private_key, jwk = _generate_rsa_keypair()

    settings = _build_settings(
        ARIAOPS_HTTP_OAUTH_ENABLED=True,
        ARIAOPS_HTTP_OAUTH_ISSUER_URL="https://kc.example.com/realms/myrealm",
        ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL="https://mcp.example.com",
        ARIAOPS_HTTP_OAUTH_JWKS_URL="https://kc.example.com/realms/myrealm/protocol/openid-connect/certs",
        ARIAOPS_HTTP_OAUTH_JWT_ALGORITHMS=["RS256"],
        ARIAOPS_HTTP_OAUTH_AUDIENCE="mcp-client",
    )
    verifier = JWTTokenVerifier(settings)

    # Stub PyJWKClient.get_signing_key_from_jwt so we don't make a real HTTP request
    from jwt import PyJWK

    def fake_get_signing_key_from_jwt(_self, _token):
        return PyJWK(jwk)

    from jwt import PyJWKClient

    monkeypatch.setattr(PyJWKClient, "get_signing_key_from_jwt", fake_get_signing_key_from_jwt)

    now = datetime.now(UTC)
    # Keycloak-shaped claims: iss is realm URL, aud is client ID, azp present, scope as space-string
    token = _sign_rs256(
        private_key,
        {
            "iss": "https://kc.example.com/realms/myrealm",
            "aud": ["mcp-client", "account"],  # multi-aud is common in KC
            "azp": "mcp-client",
            "scope": "openid mcp:read",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "typ": "Bearer",
        },
    )

    access = await verifier.verify_token(token)
    assert access is not None
    assert access.client_id == "mcp-client"
    assert "mcp:read" in access.scopes


@pytest.mark.asyncio
async def test_verifier_keycloak_provider_defaults_verify_rs256_token(monkeypatch):
    """Keycloak provider mode derives the realm JWKS URL and defaults to RS256."""
    private_key, jwk = _generate_rsa_keypair()

    settings = _build_settings(
        ARIAOPS_HTTP_OAUTH_ENABLED=True,
        ARIAOPS_HTTP_OAUTH_PROVIDER="keycloak",
        ARIAOPS_HTTP_OAUTH_ISSUER_URL="https://kc.example.com/realms/myrealm",
        ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL="https://mcp.example.com",
        ARIAOPS_HTTP_OAUTH_AUDIENCE="mcp-client",
    )
    verifier = JWTTokenVerifier(settings)

    from jwt import PyJWK, PyJWKClient

    def fake_get_signing_key_from_jwt(_self, _token):
        return PyJWK(jwk)

    monkeypatch.setattr(PyJWKClient, "get_signing_key_from_jwt", fake_get_signing_key_from_jwt)

    now = datetime.now(UTC)
    token = _sign_rs256(
        private_key,
        {
            "iss": "https://kc.example.com/realms/myrealm",
            "aud": "mcp-client",
            "azp": "mcp-client",
            "scope": "mcp:read",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
    )

    access = await verifier.verify_token(token)
    assert access is not None
    assert settings.http_oauth_jwt_algorithms == ["RS256"]
    assert str(settings.http_oauth_jwks_url) == "https://kc.example.com/realms/myrealm/protocol/openid-connect/certs"


@pytest.mark.asyncio
async def test_verifier_jwks_lookup_failure_rejects(monkeypatch):
    """If JWKS lookup raises, verify_token must return None (not propagate)."""
    settings = _build_settings(
        ARIAOPS_HTTP_OAUTH_ENABLED=True,
        ARIAOPS_HTTP_OAUTH_ISSUER_URL="https://kc.example.com/realms/myrealm",
        ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL="https://mcp.example.com",
        ARIAOPS_HTTP_OAUTH_JWKS_URL="https://kc.example.com/realms/myrealm/protocol/openid-connect/certs",
        ARIAOPS_HTTP_OAUTH_JWT_ALGORITHMS=["RS256"],
    )
    verifier = JWTTokenVerifier(settings)

    from jwt import PyJWKClient, PyJWKClientError

    def fake_lookup(_self, _token):
        raise PyJWKClientError("simulated network failure")

    monkeypatch.setattr(PyJWKClient, "get_signing_key_from_jwt", fake_lookup)

    # Token content doesn't matter — JWKS lookup happens first.
    assert await verifier.verify_token("doesnt.matter.here") is None


# ---------------------------------------------------------------------------
# Health-degraded case
# ---------------------------------------------------------------------------


class _UnhealthyClient:
    class _CircuitBreaker:
        class _State:
            value = "open"

        state = _State()

    circuit_breaker = _CircuitBreaker()

    async def get(self, _path: str):
        raise RuntimeError("upstream unreachable")

    async def close(self) -> None:
        return None


def test_health_endpoint_returns_503_when_upstream_fails():
    settings = _build_settings()
    app = create_http_app(server=object(), settings=settings, session_manager=_FakeSessionManager())
    token = set_client_override(_UnhealthyClient())

    try:
        with TestClient(app) as client:
            response = client.get("/health")
    finally:
        reset_client_override(token)

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
