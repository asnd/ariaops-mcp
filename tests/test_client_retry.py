"""Tests for AriaOpsClient retry and token-refresh behavior."""

import asyncio

import httpx
import pytest
import respx

from ariaops_mcp.client import AriaOpsClient
from tests.conftest import TOKEN_RESPONSE

BASE = "https://vrops.test.local/suite-api/api"


@pytest.mark.asyncio
async def test_get_retries_on_transient_error_then_succeeds(mock_env, monkeypatch):
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr("ariaops_mcp.client.asyncio.sleep", fake_sleep)

    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        version_route = respx.get(f"{BASE}/versions/current").mock(
            side_effect=[
                httpx.Response(503, json={"error": "temporary"}),
                httpx.Response(200, json={"releaseName": "8.18.0"}),
            ]
        )

        client = AriaOpsClient()
        result = await client.get("/versions/current")

        assert result["releaseName"] == "8.18.0"
        assert version_route.call_count == 2
        assert sleep_calls == [0.5]
        await client.close()


@pytest.mark.asyncio
async def test_get_raises_after_retry_budget_exhausted(mock_env, monkeypatch):
    async def fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("ariaops_mcp.client.asyncio.sleep", fake_sleep)

    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
        version_route = respx.get(f"{BASE}/versions/current").mock(
            return_value=httpx.Response(503, json={"error": "down"})
        )

        client = AriaOpsClient()
        with pytest.raises(httpx.HTTPStatusError):
            await client.get("/versions/current")

        # Initial request + 3 retries.
        assert version_route.call_count == 4
        await client.close()


@pytest.mark.asyncio
async def test_token_acquired_once_during_parallel_calls(mock_env):
    with respx.mock:
        token_route = respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/versions/current").mock(return_value=httpx.Response(200, json={"ok": True}))

        client = AriaOpsClient()
        await asyncio.gather(
            client.get("/versions/current"),
            client.get("/versions/current"),
            client.get("/versions/current"),
        )

        assert token_route.call_count == 1
        await client.close()
