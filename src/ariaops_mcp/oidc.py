"""OIDC discovery for the HTTP OAuth verifier.

When ``ARIAOPS_HTTP_OAUTH_DISCOVERY=true``, the JWKS URL and accepted signing
algorithms are read from the issuer's ``/.well-known/openid-configuration``
document instead of being configured manually. Discovery runs once at startup
(before uvicorn begins serving), so a failure aborts the process with a clear
error rather than producing a server that rejects every token. JWKS key
rotation after startup is still handled by ``PyJWKClient``'s TTL cache.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ariaops_mcp.http_auth import _normalize_url_claim


class OIDCDiscoveryError(RuntimeError):
    """Raised when the OIDC discovery document cannot be fetched or is invalid."""


@dataclass(frozen=True)
class OIDCDiscoveryResult:
    issuer: str
    jwks_uri: str
    # Asymmetric signing algorithms advertised by the issuer (HS* filtered out:
    # JWKS only distributes public keys, so HMAC can never be verified from it).
    algorithms: list[str]


def discover_oidc_config(issuer_url: str, *, timeout: float = 10.0) -> OIDCDiscoveryResult:
    """Fetch and validate ``<issuer>/.well-known/openid-configuration``."""
    discovery_url = f"{issuer_url.rstrip('/')}/.well-known/openid-configuration"

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(discovery_url)
    except httpx.HTTPError as exc:
        raise OIDCDiscoveryError(
            f"OIDC discovery failed: could not fetch {discovery_url}: {exc}"
        ) from exc

    if response.status_code != 200:
        raise OIDCDiscoveryError(
            f"OIDC discovery failed: {discovery_url} returned HTTP {response.status_code}"
        )

    try:
        document = response.json()
    except ValueError as exc:
        raise OIDCDiscoveryError(
            f"OIDC discovery failed: {discovery_url} did not return valid JSON"
        ) from exc
    if not isinstance(document, dict):
        raise OIDCDiscoveryError(
            f"OIDC discovery failed: {discovery_url} did not return a JSON object"
        )

    document_issuer = _normalize_url_claim(document.get("issuer"))
    expected_issuer = _normalize_url_claim(issuer_url)
    if not document_issuer or document_issuer != expected_issuer:
        raise OIDCDiscoveryError(
            f"OIDC discovery failed: discovery document issuer '{document.get('issuer')}' "
            f"does not match ARIAOPS_HTTP_OAUTH_ISSUER_URL '{issuer_url}'"
        )

    jwks_uri = document.get("jwks_uri")
    if not jwks_uri or not isinstance(jwks_uri, str):
        raise OIDCDiscoveryError(
            f"OIDC discovery failed: no jwks_uri in discovery document at {discovery_url}"
        )
    if not jwks_uri.lower().startswith("https://"):
        raise OIDCDiscoveryError(
            f"OIDC discovery failed: jwks_uri must use https://, got '{jwks_uri}'"
        )

    advertised = document.get("id_token_signing_alg_values_supported") or []
    algorithms = [
        str(alg)
        for alg in advertised
        if str(alg).lower() != "none" and not str(alg).upper().startswith("HS")
    ]
    if not algorithms:
        raise OIDCDiscoveryError(
            "OIDC discovery failed: the issuer advertises no asymmetric signing "
            "algorithms (id_token_signing_alg_values_supported). Set "
            "ARIAOPS_HTTP_OAUTH_JWT_ALGORITHMS explicitly if the issuer's "
            "metadata is incomplete."
        )

    return OIDCDiscoveryResult(
        issuer=document_issuer,
        jwks_uri=jwks_uri,
        algorithms=algorithms,
    )
