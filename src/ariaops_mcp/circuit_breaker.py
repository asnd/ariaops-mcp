"""Async circuit breaker for Aria Operations API resilience.

States:
    CLOSED  — requests pass through normally; consecutive failures are counted.
    OPEN    — requests are immediately rejected; transitions to HALF_OPEN after recovery timeout.
    HALF_OPEN — a limited number of probe requests are allowed through to test recovery.
"""

from __future__ import annotations

import logging
import time
from enum import StrEnum

logger = logging.getLogger(__name__)


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a request is rejected because the circuit is open."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"Circuit breaker is open. Retry after {retry_after:.1f}s.")


class CircuitBreaker:
    """Async-safe circuit breaker (single-threaded asyncio event loop)."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 30,
        success_threshold: int = 2,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._success_threshold = success_threshold

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at: float = 0.0

    @property
    def state(self) -> CircuitState:
        """Current circuit state (may auto-transition from OPEN to HALF_OPEN)."""
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self._recovery_timeout:
                self._transition_to(CircuitState.HALF_OPEN)
        return self._state

    def check(self) -> None:
        """Raise CircuitOpenError if the circuit is open and recovery timeout not elapsed."""
        current = self.state  # triggers auto-transition check
        if current == CircuitState.OPEN:
            elapsed = time.monotonic() - self._opened_at
            retry_after = max(0.0, self._recovery_timeout - elapsed)
            raise CircuitOpenError(retry_after=retry_after)

    def record_success(self) -> None:
        """Record a successful request."""
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            logger.debug(
                "Circuit half-open: success %d/%d",
                self._success_count,
                self._success_threshold,
            )
            if self._success_count >= self._success_threshold:
                self._transition_to(CircuitState.CLOSED)
        elif self._state == CircuitState.CLOSED:
            # Reset consecutive failure count on any success
            self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed request (5xx, timeout, network error)."""
        if self._state == CircuitState.HALF_OPEN:
            # Probe failed — reopen immediately
            self._transition_to(CircuitState.OPEN)
        elif self._state == CircuitState.CLOSED:
            self._failure_count += 1
            logger.debug(
                "Circuit closed: failure %d/%d",
                self._failure_count,
                self._failure_threshold,
            )
            if self._failure_count >= self._failure_threshold:
                self._transition_to(CircuitState.OPEN)

    def _transition_to(self, new_state: CircuitState) -> None:
        old_state = self._state
        self._state = new_state
        logger.warning("Circuit breaker: %s -> %s", old_state.value, new_state.value)

        if new_state == CircuitState.OPEN:
            self._opened_at = time.monotonic()
            self._success_count = 0
        elif new_state == CircuitState.HALF_OPEN:
            self._success_count = 0
        elif new_state == CircuitState.CLOSED:
            self._failure_count = 0
            self._success_count = 0
