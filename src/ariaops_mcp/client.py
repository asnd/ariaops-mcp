"""Aria Operations HTTP client with token lifecycle management."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from ariaops_mcp.config import get_settings

logger = logging.getLogger(__name__)

_TOKEN_REFRESH_BUFFER_SECS = 300  # refresh 5 min before expiry
_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}
_MAX_RETRIES = 3
_BASE_BACKOFF_SECS = 0.5


class AriaOpsClient:
    def __init__(self) -> None:
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._http: httpx.AsyncClient | None = None
        self._token_lock = asyncio.Lock()

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=get_settings().base_url,
                verify=get_settings().verify_ssl,
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
            # validity is in ms since epoch
            self._token_expiry = data.get("validity", 0) / 1000.0
            logger.debug("Token acquired, expires at %s", self._token_expiry)

    async def _request_with_retry(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        http = await self._get_http()
        attempt = 0
        last_exc: httpx.HTTPError | None = None

        while attempt <= _MAX_RETRIES:
            try:
                resp = await http.request(method, path, **kwargs)
                if resp.status_code not in _RETRYABLE_STATUS_CODES:
                    resp.raise_for_status()
                    return resp

                if attempt == _MAX_RETRIES:
                    resp.raise_for_status()

                backoff_secs = _BASE_BACKOFF_SECS * (2**attempt)
                logger.warning(
                    "%s %s returned %s, retrying in %.1fs (%s/%s)",
                    method,
                    path,
                    resp.status_code,
                    backoff_secs,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                await asyncio.sleep(backoff_secs)
                attempt += 1
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in _RETRYABLE_STATUS_CODES and attempt < _MAX_RETRIES:
                    backoff_secs = _BASE_BACKOFF_SECS * (2**attempt)
                    logger.warning(
                        "%s %s failed with %s, retrying in %.1fs (%s/%s)",
                        method,
                        path,
                        exc.response.status_code,
                        backoff_secs,
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    await asyncio.sleep(backoff_secs)
                    attempt += 1
                    continue
                raise
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    backoff_secs = _BASE_BACKOFF_SECS * (2**attempt)
                    logger.warning(
                        "%s %s request error: %s, retrying in %.1fs (%s/%s)",
                        method,
                        path,
                        exc,
                        backoff_secs,
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    await asyncio.sleep(backoff_secs)
                    attempt += 1
                    continue
                raise

        if last_exc:
            raise last_exc
        raise RuntimeError(f"Request failed unexpectedly: {method} {path}")

    async def get(self, path: str, **params: Any) -> Any:
        await self._ensure_token()
        start = time.monotonic()
        resp = await self._request_with_retry(
            "GET",
            path,
            params={k: v for k, v in params.items() if v is not None},
            headers={"Authorization": f"vRealizeOpsToken {self._token}"},
        )
        duration_ms = (time.monotonic() - start) * 1000
        logger.debug("GET %s -> %s (%.0fms)", path, resp.status_code, duration_ms)
        return resp.json()

    async def post(self, path: str, body: dict[str, Any], **params: Any) -> Any:
        await self._ensure_token()
        start = time.monotonic()
        resp = await self._request_with_retry(
            "POST",
            path,
            json=body,
            params={k: v for k, v in params.items() if v is not None},
            headers={"Authorization": f"vRealizeOpsToken {self._token}"},
        )
        duration_ms = (time.monotonic() - start) * 1000
        logger.debug("POST %s -> %s (%.0fms)", path, resp.status_code, duration_ms)
        return resp.json()

    async def get_bytes(self, path: str) -> bytes:
        await self._ensure_token()
        resp = await self._request_with_retry(
            "GET",
            path,
            headers={"Authorization": f"vRealizeOpsToken {self._token}"},
        )
        return resp.content

    async def close(self) -> None:
        if self._token and self._http:
            try:
                await self._http.post(
                    "/auth/token/release",
                    headers={"Authorization": f"vRealizeOpsToken {self._token}"},
                )
                logger.debug("Token released")
            except Exception:
                pass
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
