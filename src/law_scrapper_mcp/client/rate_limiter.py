"""Token bucket bounding the egress rate towards the Sejm API (finding F27).

The API is run by a state institution, not a contracted vendor, so the duty not to
overload it sits entirely on this side of the wire. The semaphore in `sejm_client`
bounds how many requests are in flight; this module bounds how fast they leave.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from time import monotonic

logger = logging.getLogger(__name__)


class EgressPaceDeadlineError(Exception):
    """The next pacing wait would end past the caller's deadline.

    Raised instead of sitting the wait out. The caller would have run out of budget
    anyway, and waiting first is strictly worse: it holds a circuit-breaker probe slot
    for the whole window, so other callers are refused with "API unavailable" while the
    real condition is that we are pacing ourselves.

    Carries the refused wait, and whether it came from a server-requested pause or from
    ordinary queueing for a token. The translating layer needs both: only the first is a
    halt, and telling an agent that traffic is "halted" when it is merely being paced
    describes a condition that does not exist.
    """

    def __init__(self, wait: float, *, paused: bool) -> None:
        cause = "a server-requested pause" if paused else "queueing for a token"
        super().__init__(f"Egress pacing ({cause}) would wait {wait:.1f}s past the operation deadline.")
        self.wait = wait
        self.paused = paused


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

    def __init__(
        self,
        rate: float,
        burst: int,
        *,
        clock: Callable[[], float] = monotonic,
        max_pause: float = 60.0,
    ) -> None:
        if rate <= 0:
            raise ValueError("Tempo żądań musi być większe od zera.")
        if burst < 1:
            raise ValueError("Pojemność zbiornika tokenów musi wynosić co najmniej 1.")
        if max_pause <= 0:
            raise ValueError("Maksymalna pauza na żądanie serwera musi być większa od zera.")
        self._rate = rate
        self._burst = float(burst)
        self._tokens = float(burst)
        self._clock = clock
        self._max_pause = max_pause
        self._updated = clock()
        self._paused_until = 0.0
        self._lock = asyncio.Lock()

    @property
    def clock(self) -> Callable[[], float]:
        """The clock every deadline handed to `acquire` must be measured on.

        Exposed so a client that receives a prebuilt limiter can compute its deadlines
        on the same scale instead of assuming the limiter runs on `time.monotonic()`.
        """
        return self._clock

    @property
    def max_pause(self) -> float:
        """Longest pause one server signal may impose, in seconds."""
        return self._max_pause

    def pause_for(self, seconds: float) -> None:
        """Hold every request back for `seconds`, at the server's request.

        Deliberately synchronous and lock-free: with no await point it runs to
        completion, so no other task can observe a half-applied pause, and the retry
        loop that calls it never has to await the limiter's lock. Extending rather than
        replacing the window keeps a short second signal from cutting a long first one.

        Clamped to `max_pause` here rather than by the caller: the pause outlives the
        request that received the header and holds every other waiter's `acquire()`,
        so the bound on what one signal can cost belongs to the object that applies it.
        The log line lives here for the same reason — only this side knows the length
        actually held. A non-positive request (`Retry-After: 0` is legal and means "go
        ahead") is neither applied nor logged: announcing a 0.0 s hold would describe
        a pause that never happens.
        """
        if seconds <= 0:
            return
        if seconds > self._max_pause:
            logger.warning(
                "Server-requested pause of %.1fs exceeds the %.1fs cap; pausing egress for %.1fs instead.",
                seconds,
                self._max_pause,
                self._max_pause,
            )
            seconds = self._max_pause
        else:
            logger.warning("Pausing egress for %.1fs (server requested via Retry-After).", seconds)
        self._paused_until = max(self._paused_until, self._clock() + seconds)
        # Bank what has been earned up to now before moving the refill mark forward:
        # skipping this would forfeit the tokens accrued since the last `_refill`, which
        # are pre-pause credit and belong to the caller.
        self._refill(self._clock())
        # Refill then resumes at the end of the quiet window, not before it. `_refill` is
        # never called while paused, so without this the first refill afterwards sees
        # the whole pause as elapsed time and clamps the bucket to a full burst — the
        # herd this pause exists to prevent, moved to the moment the window lifts.
        # Tokens earned before the pause survive on purpose: those are D1's happy path.
        self._updated = max(self._updated, self._paused_until)

    def _refill(self, now: float) -> None:
        elapsed = max(now - self._updated, 0.0)
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._updated = now

    def _refuse_past_deadline(self, now: float, wait: float, deadline: float | None, *, paused: bool) -> None:
        """Raise rather than start a wait that ends past `deadline`.

        Compared with `>=` on the model of the retry loop's own give-up check: a wait
        landing exactly on the deadline leaves nothing for the request itself.
        """
        if deadline is not None and now + wait >= deadline:
            raise EgressPaceDeadlineError(wait, paused=paused)

    async def acquire(self, *, deadline: float | None = None) -> None:
        """Return once this request may go out, waiting for a token if it must.

        The wait happens while holding the lock, which serialises waiters into arrival
        order and hands each one an exact deficit to sleep off. Releasing the lock
        before sleeping would wake every waiter onto the same single token.

        `deadline` is an instant on THIS limiter's `clock()`, which the caller reads
        through the `clock` property rather than assuming `time.monotonic()`: two
        unrelated scales would make the bound fail silently open rather than loudly.
        Absolute instants are kept rather than durations so that a pause extended
        mid-wait is re-measured correctly. It is checked before every wait, not once on
        entry, because a pause may be extended by another task while this one is
        already waiting. `None` means the caller accepts an unbounded wait.

        Raises:
            EgressPaceDeadlineError: The next wait would end past `deadline`.
        """
        async with self._lock:
            while True:
                now = self._clock()
                pause = self._paused_until - now
                if pause > 0:
                    self._refuse_past_deadline(now, pause, deadline, paused=True)
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
                wait = deficit / self._rate
                self._refuse_past_deadline(now, wait, deadline, paused=False)
                await _wait(wait)
