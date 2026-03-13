"""Aria Operations HTTP client with token lifecycle management."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ariaops_mcp.config import settings

logger = logging.getLogger(__name__)

_TOKEN_REFRESH_BUFFER_SECS = 300  # refresh 5 min before expiry


class AriaOpsClient:
    def __init__(self) -> None:
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
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

        http = await self._get_http()
        logger.debug("Acquiring Aria Operations auth token")
        resp = await http.post(
            "/auth/token/acquire",
            json={
                "username": settings.username,
                "password": settings.password,
                "authSource": settings.auth_source,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["token"]
        # validity is in ms since epoch
        self._token_expiry = data.get("validity", 0) / 1000.0
        logger.debug("Token acquired, expires at %s", self._token_expiry)

    async def get(self, path: str, **params: Any) -> Any:
        await self._ensure_token()
        http = await self._get_http()
        start = time.monotonic()
        resp = await http.get(
            path,
            params={k: v for k, v in params.items() if v is not None},
            headers={"Authorization": f"vRealizeOpsToken {self._token}"},
        )
        duration_ms = (time.monotonic() - start) * 1000
        logger.debug("GET %s -> %s (%.0fms)", path, resp.status_code, duration_ms)
        resp.raise_for_status()
        return resp.json()

    async def post(self, path: str, body: dict[str, Any], **params: Any) -> Any:
        await self._ensure_token()
        http = await self._get_http()
        start = time.monotonic()
        resp = await http.post(
            path,
            json=body,
            params={k: v for k, v in params.items() if v is not None},
            headers={"Authorization": f"vRealizeOpsToken {self._token}"},
        )
        duration_ms = (time.monotonic() - start) * 1000
        logger.debug("POST %s -> %s (%.0fms)", path, resp.status_code, duration_ms)
        resp.raise_for_status()
        return resp.json()

    async def get_bytes(self, path: str) -> bytes:
        await self._ensure_token()
        http = await self._get_http()
        resp = await http.get(
            path,
            headers={"Authorization": f"vRealizeOpsToken {self._token}"},
        )
        resp.raise_for_status()
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
