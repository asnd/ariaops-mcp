"""Aria Operations HTTP client with token lifecycle management and resilience.

Supports both legacy single-client mode (get_client()) and multi-instance
client pool (ClientPool) for the multi-instance architecture.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextvars import ContextVar, Token
from typing import Any

import httpx

from ariaops_mcp.circuit_breaker import CircuitBreaker, CircuitState

logger = logging.getLogger(__name__)

_TOKEN_REFRESH_BUFFER_SECS = 300  # refresh 5 min before expiry
_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}
_MAX_ATTEMPTS = 4
_BASE_BACKOFF_SECS = 0.5


class AriaOpsClient:
    """HTTP client for a single Aria Operations instance."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        auth_source: str | None = None,
        verify_ssl: bool | None = None,
        trust_env: bool | None = None,
        max_concurrent_requests: int | None = None,
        request_deadline: float | None = None,
        cb_failure_threshold: int | None = None,
        cb_recovery_timeout: int | None = None,
        cb_success_threshold: int | None = None,
        instance_name: str = "default",
    ) -> None:
        # When no explicit params provided, read from settings (backward compat)
        if base_url is None:
            try:
                from ariaops_mcp.config import get_settings
                settings = get_settings()
                base_url = settings.base_url
                username = username or settings.username
                password = password or settings.password
                auth_source = auth_source if auth_source is not None else settings.auth_source
                verify_ssl = verify_ssl if verify_ssl is not None else settings.verify_ssl
                trust_env = trust_env if trust_env is not None else settings.trust_env
                max_concurrent_requests = (
                    max_concurrent_requests if max_concurrent_requests is not None
                    else settings.max_concurrent_requests
                )
                request_deadline = (
                    request_deadline if request_deadline is not None
                    else settings.request_deadline
                )
                cb_failure_threshold = (
                    cb_failure_threshold if cb_failure_threshold is not None
                    else settings.cb_failure_threshold
                )
                cb_recovery_timeout = (
                    cb_recovery_timeout if cb_recovery_timeout is not None
                    else settings.cb_recovery_timeout
                )
                cb_success_threshold = (
                    cb_success_threshold if cb_success_threshold is not None
                    else settings.cb_success_threshold
                )
            except Exception:
                pass

        # Apply defaults for any still-None values
        auth_source = auth_source or "local"
        verify_ssl = verify_ssl if verify_ssl is not None else True
        trust_env = trust_env if trust_env is not None else True
        max_concurrent_requests = max_concurrent_requests or 10
        request_deadline = request_deadline or 120.0
        cb_failure_threshold = cb_failure_threshold or 5
        cb_recovery_timeout = cb_recovery_timeout or 30
        cb_success_threshold = cb_success_threshold or 2

        # Store connection params for lazy initialization
        self._base_url = base_url
        self._username = username
        self._password = password
        self._auth_source = auth_source
        self._verify_ssl = verify_ssl
        self._trust_env = trust_env
        self._instance_name = instance_name

        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._http: httpx.AsyncClient | None = None
        self._token_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=cb_failure_threshold,
            recovery_timeout=cb_recovery_timeout,
            success_threshold=cb_success_threshold,
        )
        self._request_deadline = request_deadline
        self._last_request_time: float = 0.0

    @classmethod
    def from_settings(cls) -> AriaOpsClient:
        """Create a client from legacy Settings (backward compatibility).
        
        Note: AriaOpsClient() with no args also reads from settings,
        but this factory is explicit and used by get_client().
        """
        return cls()

    @classmethod
    def from_instance_config(cls, instance_config: Any) -> AriaOpsClient:
        """Create a client from an InstanceConfig object."""
        from ariaops_mcp.instances import InstanceConfig

        assert isinstance(instance_config, InstanceConfig)

        # Get server-level defaults for resilience settings
        try:
            from ariaops_mcp.config import get_settings
            settings = get_settings()
            max_concurrent = settings.max_concurrent_requests
            deadline = settings.request_deadline
            default_cb_failure = settings.cb_failure_threshold
            default_cb_recovery = settings.cb_recovery_timeout
            default_cb_success = settings.cb_success_threshold
        except Exception:
            max_concurrent = 10
            deadline = 120.0
            default_cb_failure = 5
            default_cb_recovery = 30
            default_cb_success = 2

        return cls(
            base_url=instance_config.base_url,
            username=instance_config.get_username(),
            password=instance_config.get_password(),
            auth_source=instance_config.get_auth_source(),
            verify_ssl=instance_config.verify_ssl,
            max_concurrent_requests=max_concurrent,
            request_deadline=deadline,
            cb_failure_threshold=instance_config.cb_failure_threshold or default_cb_failure,
            cb_recovery_timeout=instance_config.cb_recovery_timeout or default_cb_recovery,
            cb_success_threshold=instance_config.cb_success_threshold or default_cb_success,
            instance_name=instance_config.name,
        )

    @property
    def instance_name(self) -> str:
        return self._instance_name

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        """Expose circuit breaker for testing and observability."""
        return self._circuit_breaker

    @property
    def last_request_time(self) -> float:
        """Epoch time of the last successful request."""
        return self._last_request_time

    @property
    def has_token(self) -> bool:
        """Whether the client currently holds a valid token."""
        return self._token is not None and time.time() < self._token_expiry

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            if self._base_url is None:
                # Legacy fallback
                from ariaops_mcp.config import get_settings
                settings = get_settings()
                self._base_url = settings.base_url
                self._verify_ssl = settings.verify_ssl
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                verify=self._verify_ssl,
                trust_env=self._trust_env,
                timeout=httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=30.0),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
        return self._http

    async def _ensure_token(self) -> None:
        now = time.time()
        if self._token and now < self._token_expiry - _TOKEN_REFRESH_BUFFER_SECS:
            return

        async with self._token_lock:
            now = time.time()
            if self._token and now < self._token_expiry - _TOKEN_REFRESH_BUFFER_SECS:
                return

            # Resolve credentials
            username = self._username
            password = self._password
            auth_source = self._auth_source

            if username is None or password is None:
                from ariaops_mcp.config import get_settings
                settings = get_settings()
                username = username or settings.username
                password = password or settings.password
                auth_source = auth_source or settings.auth_source

            logger.debug("Acquiring Aria Operations auth token for instance '%s'", self._instance_name)
            resp = await self._request_with_retry(
                "POST",
                "/auth/token/acquire",
                json={
                    "username": username,
                    "password": password,
                    "authSource": auth_source,
                },
            )
            data = resp.json()
            self._token = data["token"]
            # validity is in ms since epoch; fall back to 1-hour TTL if missing
            validity_ms = data.get("validity")
            self._token_expiry = validity_ms / 1000.0 if validity_ms else time.time() + 3600
            logger.debug("Token acquired for '%s', expires at %s", self._instance_name, self._token_expiry)

    def _invalidate_token(self) -> None:
        """Clear cached token so next request triggers reacquisition."""
        self._token = None
        self._token_expiry = 0.0

    async def _request_with_retry(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        http = await self._get_http()
        attempt = 0
        last_exc: httpx.HTTPError | None = None

        while attempt < _MAX_ATTEMPTS:
            try:
                resp = await http.request(method, path, **kwargs)
                if resp.status_code not in _RETRYABLE_STATUS_CODES:
                    resp.raise_for_status()
                    return resp

                if attempt == _MAX_ATTEMPTS - 1:
                    resp.raise_for_status()

                backoff_secs = _BASE_BACKOFF_SECS * (2**attempt)
                logger.warning(
                    "[%s] %s %s returned %s, retrying in %.1fs (%s/%s)",
                    self._instance_name,
                    method,
                    path,
                    resp.status_code,
                    backoff_secs,
                    attempt + 1,
                    _MAX_ATTEMPTS,
                )
                await asyncio.sleep(backoff_secs)
                attempt += 1
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in _RETRYABLE_STATUS_CODES and attempt < _MAX_ATTEMPTS - 1:
                    backoff_secs = _BASE_BACKOFF_SECS * (2**attempt)
                    logger.warning(
                        "[%s] %s %s failed with %s, retrying in %.1fs (%s/%s)",
                        self._instance_name,
                        method,
                        path,
                        exc.response.status_code,
                        backoff_secs,
                        attempt + 1,
                        _MAX_ATTEMPTS,
                    )
                    await asyncio.sleep(backoff_secs)
                    attempt += 1
                    continue
                raise
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < _MAX_ATTEMPTS - 1:
                    backoff_secs = _BASE_BACKOFF_SECS * (2**attempt)
                    logger.warning(
                        "[%s] %s %s request error: %s, retrying in %.1fs (%s/%s)",
                        self._instance_name,
                        method,
                        path,
                        exc,
                        backoff_secs,
                        attempt + 1,
                        _MAX_ATTEMPTS,
                    )
                    await asyncio.sleep(backoff_secs)
                    attempt += 1
                    continue
                raise

        if last_exc:
            raise last_exc
        raise RuntimeError(f"Request failed unexpectedly: {method} {path}")

    async def _authed_request(
        self, method: str, path: str, body: dict[str, Any] | None = None, response_type: str = "json", **params: Any
    ) -> Any:
        """Shared helper: ensures token, makes request with resilience.

        Args:
            response_type: Either "json" (default) or "content" for raw bytes.
        """
        # Circuit breaker gate — fails fast if backend is known-down
        self._circuit_breaker.check()

        # Concurrency limiter — blocks cooperatively if too many parallel requests
        async with self._semaphore:
            # Overall request deadline — caps total wall-clock time including retries
            async with asyncio.timeout(self._request_deadline):
                return await self._authed_request_inner(method, path, body, response_type, **params)

    async def _authed_request_inner(
        self, method: str, path: str, body: dict[str, Any] | None = None, response_type: str = "json", **params: Any
    ) -> Any:
        """Inner implementation: token management, 401 re-auth, circuit breaker recording."""
        await self._ensure_token()
        start = time.monotonic()
        kwargs: dict[str, Any] = {
            "params": {k: v for k, v in params.items() if v is not None},
            "headers": {"Authorization": f"vRealizeOpsToken {self._token}"},
        }
        if body is not None:
            kwargs["json"] = body

        try:
            resp = await self._request_with_retry(method, path, **kwargs)
        except httpx.HTTPStatusError as exc:
            # 401 Unauthorized — invalidate token and retry once
            if exc.response.status_code == 401:
                logger.warning("[%s] Received 401, invalidating token and reacquiring", self._instance_name)
                self._invalidate_token()
                await self._ensure_token()
                kwargs["headers"] = {"Authorization": f"vRealizeOpsToken {self._token}"}
                try:
                    resp = await self._request_with_retry(method, path, **kwargs)
                except Exception:
                    self._circuit_breaker.record_failure()
                    raise
            else:
                # 4xx (non-retryable) do NOT trip the circuit breaker
                if exc.response.status_code >= 500:
                    self._circuit_breaker.record_failure()
                raise
        except (httpx.HTTPError, TimeoutError, OSError):
            # Network/timeout errors count as circuit failures
            self._circuit_breaker.record_failure()
            raise

        duration_ms = (time.monotonic() - start) * 1000
        logger.debug("[%s] %s %s -> %s (%.0fms)", self._instance_name, method, path, resp.status_code, duration_ms)
        self._circuit_breaker.record_success()
        self._last_request_time = time.time()

        # Return raw bytes or JSON based on response_type
        if response_type == "content":
            return resp.content
        # Some mutating endpoints return 204 No Content
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    async def get(self, path: str, **params: Any) -> Any:
        return await self._authed_request("GET", path, **params)

    async def post(self, path: str, body: dict[str, Any], **params: Any) -> Any:
        return await self._authed_request("POST", path, body, **params)

    async def put(self, path: str, body: dict[str, Any], **params: Any) -> Any:
        return await self._authed_request("PUT", path, body, **params)

    async def delete(self, path: str, body: dict[str, Any] | None = None, **params: Any) -> Any:
        return await self._authed_request("DELETE", path, body, **params)

    async def get_bytes(self, path: str) -> bytes:
        """Fetch raw bytes (e.g., report downloads). Delegates to _authed_request."""
        return await self._authed_request("GET", path, response_type="content")

    async def close(self) -> None:
        if self._token and self._http:
            try:
                await self._http.post(
                    "/auth/token/release",
                    headers={"Authorization": f"vRealizeOpsToken {self._token}"},
                )
                logger.debug("[%s] Token released", self._instance_name)
            except Exception as e:
                logger.warning("[%s] Failed to release token: %s", self._instance_name, e)
        if self._http:
            await self._http.aclose()
            self._http = None


# ── Client Pool ───────────────────────────────────────────────────────────────


class ClientPool:
    """Pool of AriaOpsClient instances, one per configured vROps instance.

    Clients are created lazily on first access and cached.
    """

    def __init__(self) -> None:
        self._clients: dict[str, AriaOpsClient] = {}
        self._lock = asyncio.Lock()

    async def get_client(self, instance_name: str) -> AriaOpsClient:
        """Get or create a client for the specified instance."""
        if instance_name in self._clients:
            return self._clients[instance_name]

        async with self._lock:
            # Double-check after acquiring lock
            if instance_name in self._clients:
                return self._clients[instance_name]

            from ariaops_mcp.instances import get_instance_registry

            registry = get_instance_registry()
            config = registry.get(instance_name)
            if config is None:
                raise ValueError(
                    f"Instance '{instance_name}' not found in registry. "
                    f"Available: {registry.instance_names()}"
                )

            client = AriaOpsClient.from_instance_config(config)
            self._clients[instance_name] = client
            logger.info("Created client for instance '%s' (%s)", instance_name, config.host)
            return client

    def get_client_sync(self, instance_name: str) -> AriaOpsClient | None:
        """Get a client if already created (non-async, for status checks)."""
        return self._clients.get(instance_name)

    async def shutdown(self) -> None:
        """Release all tokens and close all HTTP clients."""
        for name, client in self._clients.items():
            try:
                await client.close()
                logger.debug("Closed client for instance '%s'", name)
            except Exception as e:
                logger.warning("Error closing client for instance '%s': %s", name, e)
        self._clients.clear()

    def active_instances(self) -> list[str]:
        """Return names of instances with active clients."""
        return list(self._clients.keys())

    def get_health(self, instance_name: str) -> dict[str, Any]:
        """Get health status for an instance client."""
        client = self._clients.get(instance_name)
        if client is None:
            return {"status": "not_initialized", "instance": instance_name}

        cb = client.circuit_breaker
        return {
            "instance": instance_name,
            "status": "healthy" if cb.state == CircuitState.CLOSED else cb.state.value,
            "circuit_breaker": cb.state.value,
            "has_token": client.has_token,
            "last_request_time": client.last_request_time,
        }


# ── Module-level singleton pool ───────────────────────────────────────────────

_pool: ClientPool | None = None


def get_client_pool() -> ClientPool:
    """Get or create the module-level client pool singleton."""
    global _pool
    if _pool is None:
        _pool = ClientPool()
    return _pool


def reset_client_pool() -> None:
    """Reset the client pool (for testing)."""
    global _pool
    _pool = None


# ── Backward-compatible get_client() ─────────────────────────────────────────

_client: AriaOpsClient | None = None
_client_override: ContextVar[AriaOpsClient | None] = ContextVar("ariaops_client_override", default=None)


def get_client() -> AriaOpsClient:
    """Get the default AriaOpsClient (backward compatibility).

    Resolution:
    1. ContextVar override (for testing / session isolation)
    2. Module-level singleton (created from Settings)
    """
    override = _client_override.get()
    if override is not None:
        return override
    global _client
    if _client is None:
        _client = AriaOpsClient.from_settings()
    return _client


def set_client_override(client: AriaOpsClient) -> Token[AriaOpsClient | None]:
    return _client_override.set(client)


def reset_client_override(token: Token[AriaOpsClient | None]) -> None:
    _client_override.reset(token)
