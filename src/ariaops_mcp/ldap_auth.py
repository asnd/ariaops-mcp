"""LDAP/AD authentication for the HTTP MCP transport.

MCP clients send HTTP Basic credentials; the server binds directly to LDAPS
(no service account) to verify them and reads the user's ``memberOf`` groups.
AD groups are mapped to the *role-based* claims that :mod:`ariaops_mcp.principal`
understands (``role`` / ``country`` / ``instance``), so LDAP authentication
flows through the exact same per-instance authorization as OAuth.

The backend produces an :class:`AuthenticatedUser` wrapping a claims-carrying
``AccessToken`` subclass, so ``AuthContextMiddleware`` stores it and
``server._current_claims()`` can read the mapped claims.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import TYPE_CHECKING, Any

from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from starlette.authentication import AuthCredentials, AuthenticationBackend
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from ariaops_mcp.http_auth import ClaimsAccessToken

if TYPE_CHECKING:
    from ariaops_mcp.config import Settings

logger = logging.getLogger(__name__)

# Successful-bind claims cache entries are swept once the cache grows past this.
_CACHE_MAX_ENTRIES = 1000


# ── Group → claims mapping ────────────────────────────────────────────────────


def _extract_cn(dn: str) -> str:
    """Extract the CN value from a full LDAP DN string, or return dn unchanged."""
    for part in dn.split(","):
        stripped = part.strip()
        if stripped.upper().startswith("CN="):
            return stripped[3:]
    return dn


def map_groups_to_claims(
    groups: list[str],
    group_role_map: dict[str, dict[str, str]],
    *,
    role_claim: str,
    country_claim: str,
    instance_claim: str,
    ops_role: str,
    country_role: str,
) -> dict[str, Any] | None:
    """Translate LDAP/AD group membership into principal claims.

    ``group_role_map`` maps a group (CN or full DN) to a small descriptor, e.g.::

        {"vrops-ops": {"role": "ops"},
         "vrops-se":  {"role": "country", "country": "SE"},
         "vrops-de":  {"role": "country", "instance": "de"}}

    Resolution: an ``ops`` mapping always wins (broadest access); otherwise the
    first matching ``country`` mapping is used. Returns ``None`` when no group
    matches, so the caller can deny an authenticated-but-unmapped user.
    """
    cn_map: dict[str, dict[str, str]] = {
        _extract_cn(key).lower(): descriptor for key, descriptor in group_role_map.items()
    }

    matched: list[dict[str, str]] = []
    for group_dn in groups:
        if group_dn in group_role_map:
            matched.append(group_role_map[group_dn])
            continue
        descriptor = cn_map.get(_extract_cn(group_dn).lower())
        if descriptor is not None:
            matched.append(descriptor)

    if not matched:
        return None

    # An ops mapping grants access to every instance — it wins outright.
    for descriptor in matched:
        if descriptor.get("role", "").lower() == ops_role.lower():
            return {role_claim: ops_role}

    # Otherwise use the first country/instance mapping.
    for descriptor in matched:
        if descriptor.get("role", "").lower() == country_role.lower():
            claims: dict[str, Any] = {role_claim: country_role}
            if descriptor.get("country"):
                claims[country_claim] = descriptor["country"]
            if descriptor.get("instance"):
                claims[instance_claim] = descriptor["instance"]
            return claims

    return None


# ── Authenticator ─────────────────────────────────────────────────────────────


class LDAPAuthenticator:
    """Direct-bind LDAP/AD authenticator with an in-memory claims cache.

    Binds with the user's own credentials (no service account), reads
    ``memberOf`` to derive principal claims, and caches the result for
    ``cache_ttl`` seconds. Failed binds are never cached so that a password
    change takes effect immediately.
    """

    def __init__(
        self,
        *,
        server_uri: str,
        user_dn_template: str,
        user_search_base: str,
        group_role_map: dict[str, dict[str, str]],
        role_claim: str,
        country_claim: str,
        instance_claim: str,
        ops_role: str,
        country_role: str,
        default_role: str | None = None,
        ca_cert_file: str | None = None,
        verify_tls: bool = True,
        bind_timeout: int = 10,
        cache_ttl: int = 300,
    ) -> None:
        self._server_uri = server_uri
        self._user_dn_template = user_dn_template
        self._user_search_base = user_search_base
        self._group_role_map = group_role_map
        self._role_claim = role_claim
        self._country_claim = country_claim
        self._instance_claim = instance_claim
        self._ops_role = ops_role
        self._country_role = country_role
        # When no group map is configured, every authenticated user is granted
        # this role (defaults to the ops role = all instances).
        self._default_role = default_role or ops_role
        self._ca_cert_file = ca_cert_file
        self._verify_tls = verify_tls
        self._bind_timeout = bind_timeout
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[dict[str, Any], float]] = {}
        # Keys credentials with HMAC under a per-process random key so a memory
        # dump of the cache does not allow an offline dictionary attack.
        self._cache_hmac_key = secrets.token_bytes(32)
        self._server: Any | None = None

    @property
    def cache_ttl(self) -> int:
        return self._cache_ttl

    @classmethod
    def from_settings(cls, settings: Settings) -> LDAPAuthenticator:
        assert settings.ldap_server_uri is not None
        assert settings.ldap_user_dn_template is not None
        assert settings.ldap_user_search_base is not None
        return cls(
            server_uri=settings.ldap_server_uri,
            user_dn_template=settings.ldap_user_dn_template,
            user_search_base=settings.ldap_user_search_base,
            group_role_map=settings.ldap_group_role_map,
            role_claim=settings.role_claim,
            country_claim=settings.country_claim,
            instance_claim=settings.instance_claim,
            ops_role=settings.ops_role,
            country_role=settings.country_role,
            default_role=settings.default_role,
            ca_cert_file=settings.ldap_ca_cert_file,
            verify_tls=settings.ldap_verify_tls,
            bind_timeout=settings.ldap_bind_timeout,
            cache_ttl=settings.ldap_cache_ttl,
        )

    # ── Internal helpers ──────────────────────────────────────────────────

    def _get_server(self) -> Any:
        if self._server is None:
            import ssl

            from ldap3 import Server, Tls

            tls: Any = None
            use_ssl = self._server_uri.lower().startswith("ldaps://")
            if use_ssl or self._verify_tls:
                tls = Tls(
                    ca_certs_file=self._ca_cert_file,
                    validate=ssl.CERT_REQUIRED if self._verify_tls else ssl.CERT_NONE,
                )
            self._server = Server(
                self._server_uri,
                use_ssl=use_ssl,
                tls=tls,
                connect_timeout=self._bind_timeout,
            )
        return self._server

    def _cache_key(self, username: str, password: str) -> str:
        return hmac.new(
            self._cache_hmac_key, f"{username}:{password}".encode(), hashlib.sha256
        ).hexdigest()

    def _check_cache(self, key: str) -> dict[str, Any] | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        claims, expiry = entry
        if time.time() > expiry:
            del self._cache[key]
            return None
        return claims

    def _set_cache(self, key: str, claims: dict[str, Any]) -> None:
        if len(self._cache) >= _CACHE_MAX_ENTRIES:
            now = time.time()
            self._cache = {k: v for k, v in self._cache.items() if v[1] > now}
            while len(self._cache) >= _CACHE_MAX_ENTRIES:
                # Still full of live entries: evict the soonest-expiring one.
                soonest = min(self._cache, key=lambda k: self._cache[k][1])
                del self._cache[soonest]
        self._cache[key] = (claims, time.time() + self._cache_ttl)

    def _sync_bind_and_get_groups(self, username: str, password: str) -> list[str] | None:
        """Blocking: direct-bind and read memberOf. Returns None on auth failure."""
        from ldap3 import Connection
        from ldap3.core.exceptions import LDAPException
        from ldap3.utils.conv import escape_filter_chars

        bind_dn = self._user_dn_template.replace("{username}", username)
        server = self._get_server()

        try:
            conn = Connection(
                server,
                user=bind_dn,
                password=password,
                auto_bind=True,
                read_only=True,
                raise_exceptions=True,
            )
        except LDAPException as exc:
            logger.debug("LDAP bind failed for '%s': %s", username, exc)
            return None

        try:
            # Search supports AD UPN/sAMAccountName, generic uid=, and full DN.
            # Escape filter metacharacters so a crafted username cannot widen
            # the filter and match a different user's entry (and groups).
            safe_user = escape_filter_chars(username)
            safe_dn = escape_filter_chars(bind_dn)
            search_filter = (
                f"(|(userPrincipalName={safe_user})"
                f"(sAMAccountName={safe_user})"
                f"(uid={safe_user})"
                f"(distinguishedName={safe_dn}))"
            )
            conn.search(
                search_base=self._user_search_base,
                search_filter=search_filter,
                attributes=["memberOf"],
            )
            groups: list[str] = []
            if conn.entries:
                entry = conn.entries[0]
                raw = (
                    entry.memberOf.values
                    if hasattr(entry, "memberOf") and entry.memberOf
                    else []
                )
                groups = [str(g) for g in raw]
            return groups
        except LDAPException as exc:
            logger.warning("LDAP group search failed for '%s': %s", username, exc)
            return []
        finally:
            try:
                conn.unbind()
            except Exception:
                pass

    def _claims_for_groups(self, groups: list[str]) -> dict[str, Any] | None:
        if not self._group_role_map:
            # No map configured: grant the default role to every authenticated user.
            return {self._role_claim: self._default_role}
        return map_groups_to_claims(
            groups,
            self._group_role_map,
            role_claim=self._role_claim,
            country_claim=self._country_claim,
            instance_claim=self._instance_claim,
            ops_role=self._ops_role,
            country_role=self._country_role,
        )

    # ── Public API ────────────────────────────────────────────────────────

    async def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        """Bind as *username* and return mapped principal claims, or ``None``.

        ``None`` means either the bind failed (wrong credentials) or the user
        belongs to no mapped group. Results are cached for ``cache_ttl`` seconds;
        failures are not cached.
        """
        key = self._cache_key(username, password)
        cached = self._check_cache(key)
        if cached is not None:
            logger.debug("LDAP auth cache hit for '%s'", username)
            return cached

        groups = await asyncio.to_thread(self._sync_bind_and_get_groups, username, password)
        if groups is None:
            return None

        claims = self._claims_for_groups(groups)
        if claims is None:
            logger.info("LDAP user '%s' bound but matched no mapped group; denying", username)
            return None

        self._set_cache(key, claims)
        logger.info("LDAP authenticated '%s', claims: %s", username, claims)
        return claims


# ── Starlette auth backend ────────────────────────────────────────────────────


class BasicLDAPAuthBackend(AuthenticationBackend):
    """Starlette ``AuthenticationBackend``: HTTP Basic credentials → LDAP bind.

    On success returns ``(AuthCredentials, AuthenticatedUser(ClaimsAccessToken))``
    so ``AuthContextMiddleware`` stores the token and ``server._current_claims()``
    can read the LDAP-derived role/instance claims. Returns ``None`` when no
    valid ``Authorization: Basic`` header is present or the bind fails, which
    leaves the request unauthenticated for ``BasicRequireAuthMiddleware`` to 401.
    """

    def __init__(self, authenticator: LDAPAuthenticator) -> None:
        self._authenticator = authenticator

    async def authenticate(
        self, conn: HTTPConnection
    ) -> tuple[AuthCredentials, AuthenticatedUser] | None:
        auth_header = conn.headers.get("Authorization", "")
        if not auth_header.lower().startswith("basic "):
            return None

        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            username, _, password = decoded.partition(":")
        except Exception:
            logger.debug("Malformed Basic auth header")
            return None

        if not username or not password:
            return None

        claims = await self._authenticator.authenticate(username, password)
        if claims is None:
            # Wrong credentials or no mapped group — None lets the gate send 401.
            return None

        access_token = ClaimsAccessToken(
            token="ldap",
            client_id=username,
            scopes=[],
            expires_at=int(time.time()) + self._authenticator.cache_ttl,
            claims=claims,
        )
        return AuthCredentials([]), AuthenticatedUser(access_token)


# ── Scope-gate middleware (LDAP mode) ─────────────────────────────────────────


class BasicRequireAuthMiddleware:
    """ASGI gate for LDAP mode.

    Mirrors ``RequireAuthMiddleware`` but emits ``WWW-Authenticate: Basic`` on
    401 (the correct challenge for HTTP Basic clients). Instance-level
    authorization is enforced downstream by :mod:`ariaops_mcp.principal` using
    the claims the LDAP backend attached, so this gate only requires that the
    request is authenticated.
    """

    def __init__(self, app: ASGIApp, required_scopes: list[str] | None = None) -> None:
        self._app = app
        self._required_scopes = required_scopes or []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return

        auth_user = scope.get("user")
        if not isinstance(auth_user, AuthenticatedUser):
            await self._send_error(
                send,
                status_code=401,
                error="unauthorized",
                description="Authentication required",
                www_authenticate='Basic realm="ariaops-mcp"',
            )
            return

        auth_credentials = scope.get("auth")
        for required in self._required_scopes:
            if auth_credentials is None or required not in auth_credentials.scopes:
                await self._send_error(
                    send,
                    status_code=403,
                    error="insufficient_scope",
                    description=f"Required scope: {required}",
                )
                return

        await self._app(scope, receive, send)

    @staticmethod
    async def _send_error(
        send: Send,
        status_code: int,
        error: str,
        description: str,
        www_authenticate: str | None = None,
    ) -> None:
        body = json.dumps({"error": error, "error_description": description}).encode()
        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]
        if www_authenticate:
            headers.append((b"www-authenticate", www_authenticate.encode()))

        await send({"type": "http.response.start", "status": status_code, "headers": headers})
        await send({"type": "http.response.body", "body": body})
