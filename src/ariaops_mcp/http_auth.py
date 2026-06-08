"""OAuth 2.0 bearer-token verification for the HTTP MCP transport."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken, TokenVerifier

from ariaops_mcp.config import Settings

logger = logging.getLogger(__name__)


class ClaimsAccessToken(AccessToken):
    """``AccessToken`` that also carries the decoded principal claims.

    The base ``mcp`` ``AccessToken`` (1.x) has no ``claims``/``subject`` fields,
    so the role/country/instance claims that :mod:`ariaops_mcp.principal` needs
    are preserved here. ``server._current_claims()`` reads them via ``getattr``.
    """

    subject: str | None = None
    claims: dict[str, Any] | None = None


def _normalize_url_claim(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    normalized = text.rstrip("/")
    return normalized or text


def _extract_scopes(claims: dict[str, Any]) -> list[str]:
    raw_scopes = claims.get("scope", claims.get("scp", []))
    if isinstance(raw_scopes, str):
        return [scope for scope in raw_scopes.split() if scope]
    if isinstance(raw_scopes, list):
        return [str(scope) for scope in raw_scopes if str(scope)]
    return []


class JWTTokenVerifier(TokenVerifier):
    """Validate JWT bearer tokens issued by an external OAuth 2.x provider."""

    def __init__(self, settings: Settings):
        self._issuer = _normalize_url_claim(settings.http_oauth_issuer_url) or ""
        self._audience = _normalize_url_claim(
            settings.http_oauth_audience or settings.http_oauth_resource_server_url
        )
        self._jwt_key = settings.http_oauth_jwt_key or ""
        self._algorithms = settings.http_oauth_jwt_algorithms
        self._leeway = settings.http_oauth_leeway_seconds
        self._jwks_client: PyJWKClient | None = None
        if settings.http_oauth_jwks_url is not None:
            self._jwks_client = PyJWKClient(
                str(settings.http_oauth_jwks_url),
                cache_keys=True,
                lifespan=settings.http_oauth_jwks_cache_ttl,
            )

    async def _resolve_signing_key(self, token: str) -> str:
        if self._jwks_client is None:
            return self._jwt_key
        # PyJWKClient.get_signing_key_from_jwt is sync (urllib + parsing).
        # Run off-loop so we don't block the event loop on cache misses.
        signing_key = await asyncio.to_thread(
            self._jwks_client.get_signing_key_from_jwt, token
        )
        return signing_key.key

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token or not isinstance(token, str):
            logger.warning("Rejected empty or non-string HTTP OAuth bearer token")
            return None
        try:
            key = await self._resolve_signing_key(token)
            claims = jwt.decode(
                token,
                key,
                algorithms=self._algorithms,
                leeway=self._leeway,
                options={"verify_iss": False, "verify_aud": False},
            )
        except jwt.PyJWKClientError as exc:
            logger.warning("Rejected HTTP OAuth bearer token (JWKS lookup failed): %s", exc)
            return None
        except jwt.InvalidTokenError as exc:
            logger.warning("Rejected HTTP OAuth bearer token: %s", exc)
            return None

        issuer = _normalize_url_claim(claims.get("iss"))
        if issuer != self._issuer:
            logger.warning("Rejected HTTP OAuth bearer token with unexpected issuer")
            return None

        audience_claim = claims.get("aud")
        audiences = (
            [_normalize_url_claim(item) for item in audience_claim]
            if isinstance(audience_claim, list)
            else [_normalize_url_claim(audience_claim)]
        )
        if self._audience and self._audience not in audiences:
            logger.warning("Rejected HTTP OAuth bearer token with unexpected audience")
            return None

        client_id = (
            claims.get("client_id")
            or claims.get("azp")
            or claims.get("appid")
            or claims.get("sub")
        )
        if not client_id:
            logger.warning("Rejected HTTP OAuth bearer token without client identity claim")
            return None

        return ClaimsAccessToken(
            token=token,
            client_id=str(client_id),
            scopes=_extract_scopes(claims),
            expires_at=claims.get("exp"),
            resource=self._audience,
            subject=claims.get("sub"),
            claims=claims,
        )
