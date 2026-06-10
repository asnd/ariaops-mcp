"""Tests for OIDC discovery (ARIAOPS_HTTP_OAUTH_DISCOVERY)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from pydantic import ValidationError
from starlette.testclient import TestClient

from ariaops_mcp.__main__ import create_http_app
from ariaops_mcp.config import Settings
from ariaops_mcp.oidc import OIDCDiscoveryError, discover_oidc_config

ISSUER = "https://idp.example.com/realms/myrealm"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
JWKS_URI = f"{ISSUER}/protocol/openid-connect/certs"


def _discovery_doc(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "issuer": ISSUER,
        "jwks_uri": JWKS_URI,
        "id_token_signing_alg_values_supported": ["RS256", "ES256", "HS256", "none"],
    }
    doc.update(overrides)
    return doc


def _build_settings(**overrides: Any) -> Settings:
    return Settings.model_validate(
        {
            "ARIAOPS_HOST": "vrops.test.local",
            "ARIAOPS_USERNAME": "testuser",
            "ARIAOPS_PASSWORD": "testpass",
            "ARIAOPS_VERIFY_SSL": False,
            "ARIAOPS_TRANSPORT": "http",
            "ARIAOPS_HTTP_OAUTH_ENABLED": True,
            "ARIAOPS_HTTP_OAUTH_DISCOVERY": True,
            "ARIAOPS_HTTP_OAUTH_ISSUER_URL": ISSUER,
            "ARIAOPS_HTTP_OAUTH_RESOURCE_SERVER_URL": "https://mcp.example.com",
            **overrides,
        }
    )


# ---------------------------------------------------------------------------
# discover_oidc_config unit tests
# ---------------------------------------------------------------------------


@respx.mock
def test_discovery_happy_path():
    respx.get(DISCOVERY_URL).respond(json=_discovery_doc())

    result = discover_oidc_config(ISSUER)

    assert result.jwks_uri == JWKS_URI
    # HS* and "none" are filtered out — JWKS only carries public keys.
    assert result.algorithms == ["RS256", "ES256"]


@respx.mock
def test_discovery_accepts_trailing_slash_issuer():
    respx.get(DISCOVERY_URL).respond(json=_discovery_doc())

    result = discover_oidc_config(ISSUER + "/")

    assert result.jwks_uri == JWKS_URI


@respx.mock
def test_discovery_rejects_issuer_mismatch():
    respx.get(DISCOVERY_URL).respond(json=_discovery_doc(issuer="https://evil.example.com"))

    with pytest.raises(OIDCDiscoveryError, match="does not match"):
        discover_oidc_config(ISSUER)


@respx.mock
def test_discovery_rejects_missing_jwks_uri():
    doc = _discovery_doc()
    del doc["jwks_uri"]
    respx.get(DISCOVERY_URL).respond(json=doc)

    with pytest.raises(OIDCDiscoveryError, match="no jwks_uri"):
        discover_oidc_config(ISSUER)


@respx.mock
def test_discovery_rejects_plain_http_jwks_uri():
    respx.get(DISCOVERY_URL).respond(json=_discovery_doc(jwks_uri="http://idp.example.com/certs"))

    with pytest.raises(OIDCDiscoveryError, match="https://"):
        discover_oidc_config(ISSUER)


@respx.mock
def test_discovery_rejects_non_200():
    respx.get(DISCOVERY_URL).respond(status_code=404)

    with pytest.raises(OIDCDiscoveryError, match="HTTP 404"):
        discover_oidc_config(ISSUER)


@respx.mock
def test_discovery_rejects_invalid_json():
    respx.get(DISCOVERY_URL).respond(content=b"<html>not json</html>")

    with pytest.raises(OIDCDiscoveryError, match="valid JSON"):
        discover_oidc_config(ISSUER)


@respx.mock
def test_discovery_rejects_connection_error():
    respx.get(DISCOVERY_URL).mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(OIDCDiscoveryError, match="could not fetch"):
        discover_oidc_config(ISSUER)


@respx.mock
def test_discovery_rejects_hmac_only_issuer():
    respx.get(DISCOVERY_URL).respond(
        json=_discovery_doc(id_token_signing_alg_values_supported=["HS256"])
    )

    with pytest.raises(OIDCDiscoveryError, match="no asymmetric"):
        discover_oidc_config(ISSUER)


# ---------------------------------------------------------------------------
# Settings validation
# ---------------------------------------------------------------------------


def test_settings_discovery_relaxes_key_material_requirement():
    settings = _build_settings()
    assert settings.http_oauth_discovery is True
    assert settings.http_oauth_jwt_key is None
    assert settings.http_oauth_jwks_url is None


def test_settings_discovery_rejects_jwt_key():
    with pytest.raises(ValidationError, match="incompatible with"):
        _build_settings(ARIAOPS_HTTP_OAUTH_JWT_KEY="0123456789abcdef0123456789abcdef")


def test_settings_discovery_rejects_jwks_url():
    with pytest.raises(ValidationError, match="remove ARIAOPS_HTTP_OAUTH_JWKS_URL"):
        _build_settings(ARIAOPS_HTTP_OAUTH_JWKS_URL=JWKS_URI)


def test_settings_discovery_rejects_explicit_hmac_algorithms():
    with pytest.raises(ValidationError, match="asymmetric"):
        _build_settings(ARIAOPS_HTTP_OAUTH_JWT_ALGORITHMS=["HS256"])


def test_settings_discovery_accepts_explicit_asymmetric_algorithms():
    settings = _build_settings(ARIAOPS_HTTP_OAUTH_JWT_ALGORITHMS=["RS256"])
    assert settings.http_oauth_jwt_algorithms == ["RS256"]


def test_settings_discovery_rejects_nonpositive_timeout():
    with pytest.raises(ValidationError, match="DISCOVERY_TIMEOUT"):
        _build_settings(ARIAOPS_HTTP_OAUTH_DISCOVERY_TIMEOUT=0)


# ---------------------------------------------------------------------------
# App wiring: create_http_app with discovery enabled
# ---------------------------------------------------------------------------


@respx.mock
def test_create_http_app_uses_discovered_jwks(monkeypatch):
    """End-to-end: discovery resolves JWKS, an RS256 token then verifies."""
    from contextlib import asynccontextmanager

    import jwt as pyjwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt import PyJWK, PyJWKClient

    respx.get(DISCOVERY_URL).respond(
        json=_discovery_doc(id_token_signing_alg_values_supported=["RS256"])
    )

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()

    def _b64u_uint(n: int) -> str:
        import base64

        length = (n.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode("ascii")

    jwk = {
        "kty": "RSA",
        "kid": "kid-1",
        "alg": "RS256",
        "use": "sig",
        "n": _b64u_uint(public_numbers.n),
        "e": _b64u_uint(public_numbers.e),
    }

    captured_jwks_urls: list[str] = []
    original_init = PyJWKClient.__init__

    def spy_init(self, uri, *args, **kwargs):
        captured_jwks_urls.append(uri)
        original_init(self, uri, *args, **kwargs)

    monkeypatch.setattr(PyJWKClient, "__init__", spy_init)
    monkeypatch.setattr(
        PyJWKClient, "get_signing_key_from_jwt", lambda _self, _token: PyJWK(jwk)
    )

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

    app = create_http_app(
        server=object(), settings=_build_settings(), session_manager=_FakeSessionManager()
    )

    assert captured_jwks_urls == [JWKS_URI]

    now = datetime.now(UTC)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    token = pyjwt.encode(
        {
            "iss": ISSUER,
            "aud": "https://mcp.example.com",
            "sub": "client-123",
            "scope": "mcp:read",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        pem,
        algorithm="RS256",
        headers={"kid": "kid-1"},
    )

    with TestClient(app) as client:
        response = client.post(
            "/", headers={"Authorization": f"Bearer {token}"}, json={"jsonrpc": "2.0"}
        )

    assert response.status_code == 200


@respx.mock
def test_create_http_app_discovery_failure_aborts_startup():
    respx.get(DISCOVERY_URL).respond(status_code=500)

    with pytest.raises(OIDCDiscoveryError, match="HTTP 500"):
        create_http_app(server=object(), settings=_build_settings(), session_manager=object())
