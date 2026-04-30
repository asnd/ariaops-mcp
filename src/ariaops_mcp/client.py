"""Aria Operations HTTP client with token lifecycle management and resilience."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from ariaops_mcp.circuit_breaker import CircuitBreaker
from ariaops_mcp.config import get_settings

logger = logging.getLogger(__name__)

_TOKEN_REFRESH_BUFFER_SECS = 300  # refresh 5 min before expiry
_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}
_MAX_ATTEMPTS = 4
_BASE_BACKOFF_SECS = 0.5


class AriaOpsClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._http: httpx.AsyncClient | None = None
        self._token_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=settings.cb_failure_threshold,
            recovery_timeout=settings.cb_recovery_timeout,
            success_threshold=settings.cb_success_threshold,
        )
        self._request_deadline = settings.request_deadline

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        """Expose circuit breaker for testing and observability."""
        return self._circuit_breaker

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            settings = get_settings()
            self._http = httpx.AsyncClient(
                base_url=settings.base_url,
                verify=settings.verify_ssl,
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

            logger.debug("Acquiring Aria Operations auth token")
            resp = await self._request_with_retry(
                "POST",
                "/auth/token/acquire",
                json={
                    "username": get_settings().username,
                    "password": get_settings().password,
                    "authSource": get_settings().auth_source,
                },
            )
            data = resp.json()
            self._token = data["token"]
            # validity is in ms since epoch; fall back to 1-hour TTL if missing
            validity_ms = data.get("validity")
            self._token_expiry = validity_ms / 1000.0 if validity_ms else time.time() + 3600
            logger.debug("Token acquired, expires at %s", self._token_expiry)

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
                    "%s %s returned %s, retrying in %.1fs (%s/%s)",
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
                        "%s %s failed with %s, retrying in %.1fs (%s/%s)",
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
                        "%s %s request error: %s, retrying in %.1fs (%s/%s)",
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
                logger.warning("Received 401, invalidating token and reacquiring")
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
                logger.debug("Token released")
            except Exception as e:
                logger.warning("Failed to release token: %s", e)
        if self._http:
            await self._http.aclose()
            self._http = None


# Module-level singleton
_client: AriaOpsClient | None = None


def get_client() -> AriaOpsClient:
    global _client
    if _client is None:
        _client = AriaOpsClient()
    return _client
