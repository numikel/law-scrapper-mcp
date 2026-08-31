"""Token bucket bounding the egress rate towards the Sejm API (finding F27).

The API is run by a state institution, not a contracted vendor, so the duty not to
overload it sits entirely on this side of the wire. The semaphore in `sejm_client`
bounds how many requests are in flight; this module bounds how fast they leave.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from time import monotonic


async def _wait(seconds: float) -> None:
    """Sole waiting point of the limiter.

    Extracted on the model of `sejm_client._delay` so that tests can drive the clock
    instead of sitting the delay out.
    """
    await asyncio.sleep(seconds)


class RateLimiter:
    """Token bucket with a server-driven global pause.

    Refill is computed from elapsed time rather than by a background task, so the
    limiter owns no resource the server lifespan would have to close.

    `rate` and `burst` are immutable by design (D3): the client's pace follows
    configuration, never the process's error history. A limiter that quietly stayed
    slow after an incident hours ago is the worst failure mode to diagnose.
    """

    def __init__(self, rate: float, burst: int, *, clock: Callable[[], float] = monotonic) -> None:
        if rate <= 0:
            raise ValueError("Tempo żądań musi być większe od zera.")
        if burst < 1:
            raise ValueError("Pojemność zbiornika tokenów musi wynosić co najmniej 1.")
        self._rate = rate
        self._burst = float(burst)
        self._tokens = float(burst)
        self._clock = clock
        self._updated = clock()
        self._paused_until = 0.0
        self._lock = asyncio.Lock()

    def pause_for(self, seconds: float) -> None:
        """Hold every request back for `seconds`, at the server's request.

        Deliberately synchronous and lock-free: with no await point it runs to
        completion, so no other task can observe a half-applied pause, and the retry
        loop that calls it never has to await the limiter's lock. Extending rather than
        replacing the window keeps a short second signal from cutting a long first one.
        """
        if seconds <= 0:
            return
        self._paused_until = max(self._paused_until, self._clock() + seconds)

    def _refill(self, now: float) -> None:
        elapsed = max(now - self._updated, 0.0)
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._updated = now

    async def acquire(self) -> None:
        """Return once this request may go out, waiting for a token if it must.

        The wait happens while holding the lock, which serialises waiters into arrival
        order and hands each one an exact deficit to sleep off. Releasing the lock
        before sleeping would wake every waiter onto the same single token.
        """
        async with self._lock:
            while True:
                now = self._clock()
                pause = self._paused_until - now
                if pause > 0:
                    await _wait(pause)
                    continue
                self._refill(now)
                # Tolerance for fp error in `now - self._updated` (line 61), whose magnitude
                # scales with the clock's absolute value (`ulp(now) * rate`), not the token count.
                # Without it, a test clock advancing by exactly the computed deficit spins forever.
                tolerance = max(1e-9, math.ulp(now) * self._rate)
                if self._tokens >= 1.0 - tolerance:
                    self._tokens -= 1.0
                    return
                deficit = 1.0 - self._tokens
                await _wait(deficit / self._rate)
