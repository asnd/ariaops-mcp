"""LDAP/AD authentication for the HTTP MCP transport.

Supports direct-bind (username+password) authentication over LDAPS, with
AD group membership (memberOf) mapped to ariaops:{instance}:{access} scopes
via a configurable group→scope map.

The backend produces AuthenticatedUser(AccessToken(...)) — the same type as
BearerAuthBackend — so AuthContextMiddleware and RequireAuthMiddleware work
unchanged in LDAP mode.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import time
from typing import TYPE_CHECKING, Any

from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from starlette.authentication import AuthCredentials, AuthenticationBackend
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

if TYPE_CHECKING:
    from ariaops_mcp.config import Settings

logger = logging.getLogger(__name__)


# ── Group → Scope mapping ─────────────────────────────────────────────────────


def _extract_cn(dn: str) -> str:
    """Extract the CN value from a full LDAP DN string, or return dn unchanged."""
    for part in dn.split(","):
        stripped = part.strip()
        if stripped.upper().startswith("CN="):
            return stripped[3:]
    return dn


def map_groups_to_scopes(
    groups: list[str],
    group_scope_map: dict[str, list[str]],
) -> set[str]:
    """Map LDAP/AD group DNs or CNs to ariaops scope strings.

    Resolution order per group:
    1. Exact key match (full DN or plain CN as configured).
    2. Case-insensitive CN match (strips CN= prefix from the group DN and
       from the map key, then compares lower-case).
    """
    scopes: set[str] = set()
    cn_map: dict[str, list[str]] = {
        _extract_cn(key).lower(): mapped_scopes
        for key, mapped_scopes in group_scope_map.items()
    }

    for group_dn in groups:
        if group_dn in group_scope_map:
            scopes.update(group_scope_map[group_dn])
        else:
            group_cn = _extract_cn(group_dn).lower()
            if group_cn in cn_map:
                scopes.update(cn_map[group_cn])

    return scopes


# ── Authenticator ─────────────────────────────────────────────────────────────


class LDAPAuthenticator:
    """Direct-bind LDAP/AD authenticator with in-memory result cache.

    Binds directly with the user's credentials (no service account).  After
    a successful bind it reads the user's ``memberOf`` attribute to derive
    scope assignments, then caches the result for ``cache_ttl`` seconds to
    avoid an LDAPS round-trip on every MCP request.  Failed binds are never
    cached so that a password change takes effect immediately.
    """

    def __init__(
        self,
        *,
        server_uri: str,
        user_dn_template: str,
        user_search_base: str,
        group_scope_map: dict[str, list[str]],
        ca_cert_file: str | None = None,
        verify_tls: bool = True,
        bind_timeout: int = 10,
        cache_ttl: int = 300,
    ) -> None:
        self._server_uri = server_uri
        self._user_dn_template = user_dn_template
        self._user_search_base = user_search_base
        self._group_scope_map = group_scope_map
        self._ca_cert_file = ca_cert_file
        self._verify_tls = verify_tls
        self._bind_timeout = bind_timeout
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[set[str], float]] = {}
        self._server: Any | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> LDAPAuthenticator:
        assert settings.ldap_server_uri is not None
        assert settings.ldap_user_dn_template is not None
        assert settings.ldap_user_search_base is not None
        return cls(
            server_uri=settings.ldap_server_uri,
            user_dn_template=settings.ldap_user_dn_template,
            user_search_base=settings.ldap_user_search_base,
            group_scope_map=settings.ldap_group_scope_map,
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
        return hashlib.sha256(f"{username}:{password}".encode()).hexdigest()

    def _check_cache(self, key: str) -> set[str] | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        scopes, expiry = entry
        if time.time() > expiry:
            del self._cache[key]
            return None
        return scopes

    def _set_cache(self, key: str, scopes: set[str]) -> None:
        self._cache[key] = (scopes, time.time() + self._cache_ttl)

    def _sync_bind_and_get_groups(self, username: str, password: str) -> list[str] | None:
        """Blocking: direct-bind and read memberOf. Returns None on auth failure."""
        from ldap3 import Connection
        from ldap3.core.exceptions import LDAPException

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
            search_filter = (
                f"(|(userPrincipalName={username})"
                f"(sAMAccountName={username})"
                f"(uid={username})"
                f"(distinguishedName={bind_dn}))"
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

    # ── Public API ────────────────────────────────────────────────────────

    async def authenticate(self, username: str, password: str) -> set[str] | None:
        """Bind as *username* and return mapped scopes, or ``None`` on failure.

        Results are cached for ``cache_ttl`` seconds.  Failed binds are not
        cached so that a corrected password is accepted immediately.
        """
        key = self._cache_key(username, password)
        cached = self._check_cache(key)
        if cached is not None:
            logger.debug("LDAP auth cache hit for '%s'", username)
            return cached

        groups = await asyncio.to_thread(self._sync_bind_and_get_groups, username, password)
        if groups is None:
            return None

        scopes = map_groups_to_scopes(groups, self._group_scope_map)
        self._set_cache(key, scopes)
        logger.info("LDAP authenticated '%s', scopes: %s", username, scopes)
        return scopes


# ── Starlette auth backend ────────────────────────────────────────────────────


class BasicLDAPAuthBackend(AuthenticationBackend):
    """Starlette AuthenticationBackend: HTTP Basic credentials → LDAP bind.

    Returns ``(AuthCredentials, AuthenticatedUser(AccessToken))`` on success —
    the same types produced by ``BearerAuthBackend`` — so
    ``AuthContextMiddleware`` and ``RequireAuthMiddleware`` work unchanged.
    Returns ``None`` when no ``Authorization: Basic`` header is present,
    which leaves the request unauthenticated (``RequireAuthMiddleware`` will
    then reject it with 401).
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

        scopes = await self._authenticator.authenticate(username, password)
        if scopes is None:
            # Wrong credentials — returning None lets RequireAuthMiddleware send 401.
            return None

        access_token = AccessToken(
            token="ldap",
            client_id=username,
            scopes=sorted(scopes),
            expires_at=int(time.time()) + self._authenticator._cache_ttl,
        )
        return AuthCredentials(sorted(scopes)), AuthenticatedUser(access_token)


# ── Scope-gate middleware (LDAP mode) ─────────────────────────────────────────


class BasicRequireAuthMiddleware:
    """ASGI scope-gate for LDAP mode.

    Mirrors ``RequireAuthMiddleware`` but emits ``WWW-Authenticate: Basic``
    on 401 instead of ``Bearer``, which is the correct challenge for clients
    that authenticate with HTTP Basic credentials.
    """

    def __init__(self, app: ASGIApp, required_scopes: list[str]) -> None:
        self._app = app
        self._required_scopes = required_scopes

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
        import json

        body = json.dumps({"error": error, "error_description": description}).encode()
        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]
        if www_authenticate:
            headers.append((b"www-authenticate", www_authenticate.encode()))

        await send({"type": "http.response.start", "status": status_code, "headers": headers})
        await send({"type": "http.response.body", "body": body})
