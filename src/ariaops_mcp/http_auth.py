"""OAuth 2.0 bearer-token verification for the HTTP MCP transport."""

from __future__ import annotations

import logging
from typing import Any

import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier

from ariaops_mcp.config import Settings

logger = logging.getLogger(__name__)


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

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = jwt.decode(
                token,
                self._jwt_key,
                algorithms=self._algorithms,
                options={"verify_iss": False, "verify_aud": False},
            )
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

        raw_audience = claims.get("aud")
        resource = raw_audience[0] if isinstance(raw_audience, list) and raw_audience else raw_audience

        return AccessToken(
            token=token,
            client_id=str(client_id),
            scopes=_extract_scopes(claims),
            expires_at=claims.get("exp"),
            resource=str(resource) if resource is not None else None,
        )
