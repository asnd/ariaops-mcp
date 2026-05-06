"""Tests for CircuitBreaker state machine."""

import time
from unittest.mock import patch

from ariaops_mcp.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState


def test_initial_state_is_closed():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10, success_threshold=2)
    assert cb.state == CircuitState.CLOSED


def test_stays_closed_below_threshold():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10, success_threshold=2)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED


def test_opens_at_failure_threshold():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10, success_threshold=2)
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN


def test_open_circuit_raises_error():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=60, success_threshold=1)
    cb.record_failure()
    cb.record_failure()
    try:
        cb.check()
        assert False, "Expected CircuitOpenError"
    except CircuitOpenError as e:
        assert e.retry_after > 0
        assert e.retry_after <= 60


def test_success_resets_failure_count():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10, success_threshold=2)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    # Counter should be reset; one more failure should not open
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED


def test_transitions_to_half_open_after_recovery_timeout():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=1, success_threshold=2)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN

    # Fast-forward past recovery timeout
    with patch("ariaops_mcp.circuit_breaker.time.monotonic", return_value=time.monotonic() + 2):
        assert cb.state == CircuitState.HALF_OPEN


def test_half_open_closes_after_success_threshold():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0, success_threshold=2)
    cb.record_failure()
    cb.record_failure()
    # Immediately transition to half-open (recovery_timeout=0)
    assert cb.state == CircuitState.HALF_OPEN

    cb.record_success()
    assert cb.state == CircuitState.HALF_OPEN  # not yet closed
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_half_open_reopens_on_failure():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30, success_threshold=2)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN

    # Manually transition to half-open by simulating elapsed time
    with patch("ariaops_mcp.circuit_breaker.time.monotonic", return_value=time.monotonic() + 31):
        assert cb.state == CircuitState.HALF_OPEN

    # Record a failure in half-open state — should reopen
    cb.record_failure()
    # Access internal state directly to avoid auto-transition (recovery_timeout hasn't elapsed)
    assert cb._state == CircuitState.OPEN


def test_check_passes_when_closed():
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30, success_threshold=2)
    cb.check()  # Should not raise


def test_check_passes_when_half_open():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0, success_threshold=2)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.HALF_OPEN
    cb.check()  # Should not raise — probes allowed
