"""Tests for AriaOpsClient token lifecycle."""

import httpx
import pytest
import respx

from ariaops_mcp.client import AriaOpsClient
from tests.conftest import TOKEN_RESPONSE

BASE = "https://vrops.test.local/suite-api/api"


@pytest.mark.asyncio
async def test_token_acquire(mock_env):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/versions/current").mock(
            return_value=httpx.Response(200, json={"releaseName": "8.18.0"})
        )

        c = AriaOpsClient()
        result = await c.get("/versions/current")
        assert result["releaseName"] == "8.18.0"
        assert c._token == "test-token-abc123"
        await c.close()


@pytest.mark.asyncio
async def test_token_reused_on_second_call(mock_env):
    with respx.mock:
        token_route = respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/versions/current").mock(
            return_value=httpx.Response(200, json={"releaseName": "8.18.0"})
        )

        c = AriaOpsClient()
        await c.get("/versions/current")
        await c.get("/versions/current")
        # Token should only be acquired once
        assert token_route.call_count == 1
        await c.close()


@pytest.mark.asyncio
async def test_token_release_on_close(mock_env):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/versions/current").mock(
            return_value=httpx.Response(200, json={})
        )
        release_route = respx.post(f"{BASE}/auth/token/release").mock(
            return_value=httpx.Response(204)
        )

        c = AriaOpsClient()
        await c.get("/versions/current")
        await c.close()
        assert release_route.call_count == 1
