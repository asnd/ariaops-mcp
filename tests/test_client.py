"""Tests for AriaOpsClient token lifecycle and resilience features."""

import asyncio

import httpx
import pytest
import respx

from ariaops_mcp.circuit_breaker import CircuitOpenError, CircuitState
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


@pytest.mark.asyncio
async def test_put_method(mock_env):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.put(f"{BASE}/resources/maintained").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )

        c = AriaOpsClient()
        result = await c.put("/resources/maintained", {"resourceIds": ["r1", "r2"]})
        assert result == {"status": "ok"}
        await c.close()


@pytest.mark.asyncio
async def test_put_method_204_no_content(mock_env):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.put(f"{BASE}/resources/maintained").mock(
            return_value=httpx.Response(204)
        )

        c = AriaOpsClient()
        result = await c.put("/resources/maintained", {"resourceIds": ["r1"]})
        assert result == {}
        await c.close()


@pytest.mark.asyncio
async def test_delete_method_with_body(mock_env):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.delete(f"{BASE}/resources/maintained").mock(
            return_value=httpx.Response(204)
        )

        c = AriaOpsClient()
        result = await c.delete("/resources/maintained", {"resourceIds": ["r1"]})
        assert result == {}
        await c.close()


@pytest.mark.asyncio
async def test_delete_method_no_body(mock_env):
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.delete(f"{BASE}/reports/rpt-1").mock(
            return_value=httpx.Response(200, json={"deleted": True})
        )

        c = AriaOpsClient()
        result = await c.delete("/reports/rpt-1")
        assert result == {"deleted": True}
        await c.close()


# --- 401 Re-auth tests ---


@pytest.mark.asyncio
async def test_401_triggers_token_reacquisition(mock_env):
    """On 401, the client should invalidate the token, reacquire, and retry once."""
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(401, json={"error": "Unauthorized"})
        return httpx.Response(200, json={"data": "ok"})

    with respx.mock:
        token_route = respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/resources/test-id").mock(side_effect=side_effect)

        c = AriaOpsClient()
        result = await c.get("/resources/test-id")
        assert result == {"data": "ok"}
        # Token acquired initially + re-acquired after 401 = 2
        assert token_route.call_count == 2
        await c.close()


@pytest.mark.asyncio
async def test_401_retry_fails_does_not_loop(mock_env):
    """If retry after 401 also returns 401, the error should propagate (no infinite loop)."""
    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/resources/test-id").mock(
            return_value=httpx.Response(401, json={"error": "Unauthorized"})
        )

        c = AriaOpsClient()
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await c.get("/resources/test-id")
        assert exc_info.value.response.status_code == 401
        await c.close()


# --- Circuit breaker integration tests ---


@pytest.mark.asyncio
async def test_circuit_breaker_opens_on_consecutive_5xx(mock_env, monkeypatch):
    """Circuit breaker opens after failure_threshold consecutive 5xx errors."""
    monkeypatch.setenv("ARIAOPS_CB_FAILURE_THRESHOLD", "3")
    monkeypatch.setenv("ARIAOPS_CB_RECOVERY_TIMEOUT", "60")

    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/test").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        c = AriaOpsClient()
        # Exhaust the circuit breaker threshold
        for _ in range(3):
            with pytest.raises(httpx.HTTPStatusError):
                await c.get("/test")

        # Next call should be rejected by circuit breaker without hitting the API
        assert c.circuit_breaker.state == CircuitState.OPEN
        with pytest.raises(CircuitOpenError):
            await c.get("/test")
        await c.close()


@pytest.mark.asyncio
async def test_circuit_breaker_does_not_trip_on_4xx(mock_env, monkeypatch):
    """4xx client errors should NOT trip the circuit breaker."""
    monkeypatch.setenv("ARIAOPS_CB_FAILURE_THRESHOLD", "2")

    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        # 404 is a client error — should not count as circuit failure
        # But note: our 401 handling will trigger re-auth. Use 404 instead.
        respx.get(f"{BASE}/resources/bad-id").mock(
            return_value=httpx.Response(404, text="Not Found")
        )

        c = AriaOpsClient()
        for _ in range(5):
            with pytest.raises(httpx.HTTPStatusError):
                await c.get("/resources/bad-id")

        # Circuit should still be closed
        assert c.circuit_breaker.state == CircuitState.CLOSED
        await c.close()


# --- Concurrency limiter tests ---


@pytest.mark.asyncio
async def test_concurrency_limiter_allows_configured_max(mock_env, monkeypatch):
    """Semaphore should limit concurrent requests to max_concurrent_requests."""
    monkeypatch.setenv("ARIAOPS_MAX_CONCURRENT_REQUESTS", "3")

    peak_concurrent = 0
    current_concurrent = 0

    async def track_concurrency(request):
        nonlocal peak_concurrent, current_concurrent
        current_concurrent += 1
        peak_concurrent = max(peak_concurrent, current_concurrent)
        await asyncio.sleep(0.05)
        current_concurrent -= 1
        return httpx.Response(200, json={"ok": True})

    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/test").mock(side_effect=track_concurrency)

        c = AriaOpsClient()
        # Launch 6 concurrent requests with limit of 3
        tasks = [c.get("/test") for _ in range(6)]
        await asyncio.gather(*tasks)

        assert peak_concurrent <= 3
        await c.close()


# --- Request deadline tests ---


@pytest.mark.asyncio
async def test_request_deadline_exceeded(mock_env, monkeypatch):
    """If a request exceeds the deadline, TimeoutError should be raised."""
    monkeypatch.setenv("ARIAOPS_REQUEST_DEADLINE", "0.1")  # 100ms deadline

    async def slow_response(request):
        await asyncio.sleep(1.0)  # Way longer than 100ms
        return httpx.Response(200, json={"ok": True})

    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/slow-endpoint").mock(side_effect=slow_response)

        c = AriaOpsClient()
        with pytest.raises(TimeoutError):
            await c.get("/slow-endpoint")
        await c.close()


@pytest.mark.asyncio
async def test_request_within_deadline_succeeds(mock_env, monkeypatch):
    """Requests completing within the deadline should succeed normally."""
    monkeypatch.setenv("ARIAOPS_REQUEST_DEADLINE", "5.0")

    with respx.mock:
        respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/fast-endpoint").mock(
            return_value=httpx.Response(200, json={"result": "fast"})
        )

        c = AriaOpsClient()
        result = await c.get("/fast-endpoint")
        assert result == {"result": "fast"}
        await c.close()


# --- Token acquired once during parallel calls ---


@pytest.mark.asyncio
async def test_token_acquired_once_during_parallel_calls(mock_env):
    """Multiple concurrent requests should only trigger one token acquisition."""
    with respx.mock:
        token_route = respx.post(f"{BASE}/auth/token/acquire").mock(
            return_value=httpx.Response(200, json=TOKEN_RESPONSE)
        )
        respx.get(f"{BASE}/versions/current").mock(
            return_value=httpx.Response(200, json={"releaseName": "8.18.0"})
        )

        c = AriaOpsClient()
        results = await asyncio.gather(
            c.get("/versions/current"),
            c.get("/versions/current"),
            c.get("/versions/current"),
        )
        assert all(r["releaseName"] == "8.18.0" for r in results)
        assert token_route.call_count == 1
        await c.close()
