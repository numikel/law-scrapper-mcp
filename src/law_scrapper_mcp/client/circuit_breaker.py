"""Simple circuit breaker for API protection."""

from __future__ import annotations

import logging
from enum import StrEnum
from time import monotonic

logger = logging.getLogger(__name__)


class CircuitState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Simple circuit breaker to prevent cascading failures.

    States:
        CLOSED  — normal operation, failures are counted
        OPEN    — all requests rejected immediately
        HALF_OPEN — limited test requests allowed to probe recovery

    Synchronization:
        Critical sections contain no `await`, so the event loop cannot interrupt
        them halfway — the state is atomic by construction and needs no
        `asyncio.Lock`. The condition is pinned by a test; adding `await` to any
        of `try_acquire` / `release_*` invalidates the assumption.

        Acquire/release pairing limitation (known, accepted):
        `release_success()`, `release_failure()`, and `release_probe()` determine
        their behavior based on the breaker's *current* state at release time, not
        the state when `try_acquire()` was called. A request admitted while CLOSED
        but completing after the breaker transitions to HALF_OPEN will be treated
        as a HALF_OPEN release, decrementing a probe slot it never held and — for
        `release_success()` — counting toward recovery without having passed through
        HALF_OPEN admission. This is a known architectural limitation, not a bug to
        silently hide.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 3,
    ):
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._half_open_successes = 0
        self._half_open_in_flight = 0

    @property
    def state(self) -> CircuitState:
        """Current breaker state.

        Reading is free of side effects — the OPEN → HALF_OPEN transition is
        performed by `try_acquire()` alone.
        """
        return self._state

    @property
    def failure_count(self) -> int:
        """Current number of recorded failures."""
        return self._failure_count

    def try_acquire(self) -> bool:
        """Ask for permission to perform a request.

        In HALF_OPEN the probe counter grows on admission, not on completion —
        so there is no moment in which a probe has been admitted but not yet
        counted.

        Returns:
            True if the request may be sent.
        """
        if self._state == CircuitState.OPEN and monotonic() - self._last_failure_time >= self._recovery_timeout:
            self._state = CircuitState.HALF_OPEN
            self._half_open_in_flight = 0
            self._half_open_successes = 0
            logger.info("Circuit breaker transitioning to HALF_OPEN")

        if self._state == CircuitState.CLOSED:
            return True

        if self._state == CircuitState.HALF_OPEN:
            if self._half_open_in_flight >= self._half_open_max_calls:
                return False
            self._half_open_in_flight += 1
            return True

        return False

    def release_success(self) -> None:
        """Release a probe after a successful request."""
        if self._state == CircuitState.HALF_OPEN:
            self._release_slot()
            self._half_open_successes += 1
            if self._half_open_successes >= self._half_open_max_calls:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._half_open_in_flight = 0
                self._half_open_successes = 0
                logger.info("Circuit breaker CLOSED after successful recovery")
        elif self._state == CircuitState.CLOSED:
            self._failure_count = 0

    def release_failure(self) -> None:
        """Release a probe and record an operation failure.

        Called at most once per user operation — either once attempts run out, or
        when the breaker refuses admission while a failure is already confirmed.
        The failure threshold counts failed operations, not failed network attempts.
        """
        self._failure_count += 1
        self._last_failure_time = monotonic()

        if self._state == CircuitState.HALF_OPEN:
            self._release_slot()
            self._state = CircuitState.OPEN
            logger.warning("Circuit breaker re-OPENED from HALF_OPEN after failure")
        elif self._failure_count >= self._failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker OPENED after %d failures (threshold: %d)",
                self._failure_count,
                self._failure_threshold,
            )

    def release_probe(self) -> None:
        """Release a probe without a verdict.

        Used where the event is neither a success nor a server failure — for
        example on HTTP 429, or after an attempt that will be retried.
        """
        self._release_slot()

    def _release_slot(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._half_open_in_flight = max(0, self._half_open_in_flight - 1)

    def reset(self) -> None:
        """Restore the breaker to the closed state."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._half_open_successes = 0
        self._half_open_in_flight = 0
