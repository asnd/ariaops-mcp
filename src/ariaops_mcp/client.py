"""Aria Operations HTTP client with token lifecycle management and resilience."""

from __future__ import annotations

import asyncio
import logging
import time
from contextvars import ContextVar, Token
from typing import Any

import httpx

from ariaops_mcp.circuit_breaker import CircuitBreaker
from ariaops_mcp.config import InstanceConfig, get_settings

logger = logging.getLogger(__name__)

_TOKEN_REFRESH_BUFFER_SECS = 300  # refresh 5 min before expiry
_MIN_TOKEN_REFRESH_BUFFER_SECS = 1.0
_TOKEN_REFRESH_BUFFER_RATIO = 0.1
_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}
_MAX_ATTEMPTS = 4
_BASE_BACKOFF_SECS = 0.5
_SAFE_RETRY_METHODS = {"GET", "HEAD", "OPTIONS"}
_HTTP_CONNECT_TIMEOUT_SECS = 30.0
_HTTP_READ_TIMEOUT_SECS = 60.0
_HTTP_WRITE_TIMEOUT_SECS = 30.0
_HTTP_POOL_TIMEOUT_SECS = 30.0


class AriaOpsClient:
    def __init__(self, instance: InstanceConfig | None = None) -> None:
        settings = get_settings()
        self._instance = instance or settings.get_instance()
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._token_refresh_at: float = 0.0
        self._http: httpx.AsyncClient | None = None
        self._token_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=settings.cb_failure_threshold,
            recovery_timeout=settings.cb_recovery_timeout,
            success_threshold=settings.cb_success_threshold,
        )
        self._request_deadline = settings.request_deadline
        self._http_timeout = httpx.Timeout(
            connect=_HTTP_CONNECT_TIMEOUT_SECS,
            read=_HTTP_READ_TIMEOUT_SECS,
            write=_HTTP_WRITE_TIMEOUT_SECS,
            pool=_HTTP_POOL_TIMEOUT_SECS,
        )

    @property
    def instance(self) -> InstanceConfig:
        """The Aria Operations instance this client is bound to."""
        return self._instance

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        """Expose circuit breaker for testing and observability."""
        return self._circuit_breaker

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._instance.base_url,
                verify=self._instance.verify_ssl,
                timeout=self._http_timeout,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
        return self._http

    @staticmethod
    def _compute_token_refresh_at(expiry: float, now: float) -> float:
        """Pick a refresh point shortly before expiry without invalidating short-lived tokens.

        We refresh at 10% of the observed TTL, capped at the legacy 5-minute buffer and
        floored at 1 second so very short-lived tokens are still reused briefly.
        """
        token_ttl = max(0.0, expiry - now)
        refresh_buffer = min(
            _TOKEN_REFRESH_BUFFER_SECS,
            max(_MIN_TOKEN_REFRESH_BUFFER_SECS, token_ttl * _TOKEN_REFRESH_BUFFER_RATIO),
        )
        return max(now, expiry - refresh_buffer)

    @staticmethod
    def _can_retry_request(method: str, *, idempotent: bool) -> bool:
        return method.upper() in _SAFE_RETRY_METHODS or idempotent

    def _remaining_request_budget(self) -> float | None:
        deadline_at = _request_deadline_at.get()
        if deadline_at is None:
            return None
        return max(0.0, deadline_at - time.monotonic())

    @staticmethod
    def _ensure_backoff_budget(remaining_budget: float | None, backoff_secs: float) -> None:
        """Abort retries when the remaining deadline budget cannot absorb the next backoff."""
        if remaining_budget is not None and remaining_budget <= backoff_secs:
            raise TimeoutError(
                "Insufficient request deadline budget "
                f"(remaining: {remaining_budget:.3f}s, required: {backoff_secs:.3f}s) for retry backoff"
            )

    async def _ensure_token(self) -> None:
        now = time.time()
        if self._token and now < self._token_refresh_at:
            return

        async with self._token_lock:
            now = time.time()
            if self._token and now < self._token_refresh_at:
                return

            logger.debug("Acquiring Aria Operations auth token")
            resp = await self._request_with_retry(
                "POST",
                "/auth/token/acquire",
                idempotent=True,
                json={
                    "username": self._instance.username,
                    "password": self._instance.password,
                    "authSource": self._instance.auth_source,
                },
            )
            data = resp.json()
            self._token = data["token"]
            # validity is in ms since epoch; fall back to 1-hour TTL if missing
            validity_ms = data.get("validity")
            self._token_expiry = validity_ms / 1000.0 if validity_ms else time.time() + 3600
            self._token_refresh_at = self._compute_token_refresh_at(self._token_expiry, now)
            logger.debug("Token acquired, expires at %s", self._token_expiry)

    def _invalidate_token(self) -> None:
        """Clear cached token so next request triggers reacquisition."""
        self._token = None
        self._token_expiry = 0.0
        self._token_refresh_at = 0.0

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        idempotent: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        http = await self._get_http()
        attempt = 0
        last_exc: httpx.HTTPError | None = None
        can_retry = self._can_retry_request(method, idempotent=idempotent)

        while attempt < _MAX_ATTEMPTS:
            try:
                remaining_budget = self._remaining_request_budget()
                if remaining_budget is not None:
                    if remaining_budget <= 0:
                        raise TimeoutError(
                            "Request deadline budget exhausted "
                            f"(remaining: {remaining_budget:.3f}s) before sending request"
                        )
                    async with asyncio.timeout(remaining_budget):
                        resp = await http.request(method, path, **kwargs)
                else:
                    resp = await http.request(method, path, **kwargs)
                if resp.status_code not in _RETRYABLE_STATUS_CODES or not can_retry:
                    resp.raise_for_status()
                    return resp

                if attempt == _MAX_ATTEMPTS - 1:
                    resp.raise_for_status()

                backoff_secs = _BASE_BACKOFF_SECS * (2**attempt)
                logger.warning(
                    "%s %s returned %s, retrying in %.1fs (%s/%s)",
                    method,
                    path,
                    resp.status_code,
                    backoff_secs,
                    attempt + 1,
                    _MAX_ATTEMPTS,
                )
                remaining_budget = self._remaining_request_budget()
                self._ensure_backoff_budget(remaining_budget, backoff_secs)
                await asyncio.sleep(backoff_secs)
                attempt += 1
            except httpx.HTTPStatusError as exc:
                if (
                    can_retry
                    and exc.response.status_code in _RETRYABLE_STATUS_CODES
                    and attempt < _MAX_ATTEMPTS - 1
                ):
                    backoff_secs = _BASE_BACKOFF_SECS * (2**attempt)
                    logger.warning(
                        "%s %s failed with %s, retrying in %.1fs (%s/%s)",
                        method,
                        path,
                        exc.response.status_code,
                        backoff_secs,
                        attempt + 1,
                        _MAX_ATTEMPTS,
                    )
                    remaining_budget = self._remaining_request_budget()
                    self._ensure_backoff_budget(remaining_budget, backoff_secs)
                    await asyncio.sleep(backoff_secs)
                    attempt += 1
                    continue
                raise
            except httpx.HTTPError as exc:
                last_exc = exc
                if can_retry and attempt < _MAX_ATTEMPTS - 1:
                    backoff_secs = _BASE_BACKOFF_SECS * (2**attempt)
                    logger.warning(
                        "%s %s request error: %s, retrying in %.1fs (%s/%s)",
                        method,
                        path,
                        exc,
                        backoff_secs,
                        attempt + 1,
                        _MAX_ATTEMPTS,
                    )
                    remaining_budget = self._remaining_request_budget()
                    self._ensure_backoff_budget(remaining_budget, backoff_secs)
                    await asyncio.sleep(backoff_secs)
                    attempt += 1
                    continue
                raise

        if last_exc:
            raise last_exc
        raise RuntimeError(f"Request failed unexpectedly: {method} {path}")

    async def _authed_request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        response_type: str = "json",
        idempotent: bool = False,
        **params: Any,
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
                deadline_token = _request_deadline_at.set(time.monotonic() + self._request_deadline)
                try:
                    return await self._authed_request_inner(method, path, body, response_type, idempotent, **params)
                finally:
                    _request_deadline_at.reset(deadline_token)

    async def _authed_request_inner(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        response_type: str = "json",
        idempotent: bool = False,
        **params: Any,
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
            resp = await self._request_with_retry(method, path, idempotent=idempotent, **kwargs)
        except httpx.HTTPStatusError as exc:
            # 401 Unauthorized — invalidate token and retry once
            if exc.response.status_code == 401:
                logger.warning("Received 401, invalidating token and reacquiring")
                self._invalidate_token()
                await self._ensure_token()
                kwargs["headers"] = {"Authorization": f"vRealizeOpsToken {self._token}"}
                try:
                    resp = await self._request_with_retry(method, path, idempotent=idempotent, **kwargs)
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
        logger.debug("%s %s -> %s (%.0fms)", method, path, resp.status_code, duration_ms)
        self._circuit_breaker.record_success()

        # Return raw bytes or JSON based on response_type
        if response_type == "content":
            return resp.content
        # Some mutating endpoints return 204 No Content
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    async def get(self, path: str, **params: Any) -> Any:
        return await self._authed_request("GET", path, idempotent=True, **params)

    async def post(self, path: str, body: dict[str, Any], *, idempotent: bool = False, **params: Any) -> Any:
        return await self._authed_request("POST", path, body, idempotent=idempotent, **params)

    async def put(self, path: str, body: dict[str, Any], *, idempotent: bool = False, **params: Any) -> Any:
        return await self._authed_request("PUT", path, body, idempotent=idempotent, **params)

    async def delete(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        idempotent: bool = False,
        **params: Any,
    ) -> Any:
        return await self._authed_request("DELETE", path, body, idempotent=idempotent, **params)

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
                logger.debug("Token released")
            except Exception as e:
                logger.warning("Failed to release token: %s", e)
        if self._http:
            await self._http.aclose()
            self._http = None


# --- Per-instance client registry ------------------------------------------
_clients: dict[str, AriaOpsClient] = {}
_client_override: ContextVar[AriaOpsClient | None] = ContextVar("ariaops_client_override", default=None)
_current_instance: ContextVar[str | None] = ContextVar("ariaops_current_instance", default=None)
_request_deadline_at: ContextVar[float | None] = ContextVar("ariaops_request_deadline_at", default=None)


def _resolve_instance_id(instance_id: str | None) -> str:
    """Resolve the effective instance id: explicit arg → contextvar → settings default."""
    if instance_id is not None:
        return instance_id
    current = _current_instance.get()
    if current is not None:
        return current
    return get_settings().default_instance_id


def get_client(instance_id: str | None = None) -> AriaOpsClient:
    """Return the client for the requested (or current/default) instance.

    A test override, when set, takes precedence regardless of instance so the
    existing single-client test harness keeps working.
    """
    override = _client_override.get()
    if override is not None:
        return override
    target = _resolve_instance_id(instance_id)
    client = _clients.get(target)
    if client is None:
        instance = get_settings().get_instance(target)
        client = AriaOpsClient(instance)
        _clients[target] = client
    return client


def set_current_instance(instance_id: str) -> Token[str | None]:
    return _current_instance.set(instance_id)


def reset_current_instance(token: Token[str | None]) -> None:
    _current_instance.reset(token)


def set_client_override(client: AriaOpsClient) -> Token[AriaOpsClient | None]:
    return _client_override.set(client)


def reset_client_override(token: Token[AriaOpsClient | None]) -> None:
    _client_override.reset(token)


def reset_client_cache() -> None:
    """Drop all cached per-instance clients (without closing them).

    Used by ``clear_settings_cache`` and tests. Callers that need graceful
    token release should use :func:`close_all` instead.
    """
    _clients.clear()


async def close_all() -> None:
    """Close every cached client, releasing tokens and HTTP connections."""
    clients = list(_clients.values())
    _clients.clear()
    for client in clients:
        try:
            await client.close()
        except Exception as e:  # pragma: no cover - defensive
            instance_id = getattr(getattr(client, "instance", None), "id", "unknown")
            logger.warning("Failed to close client for instance %s: %s", instance_id, e)
