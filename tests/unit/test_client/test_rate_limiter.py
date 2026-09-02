"""Outbound token bucket protecting api.sejm.gov.pl from our own pace (finding F27).

Not to be confused with `tests/unit/test_rate_limit.py`, which covers the inbound
limiter in `http/rate_limit.py`. This one is about traffic we send.

Every test drives an injected clock; none sits out a real delay (design constraint 3).
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from law_scrapper_mcp.client import rate_limiter as limiter_module
from law_scrapper_mcp.client.rate_limiter import EgressPaceDeadlineError, RateLimiter

pytestmark = pytest.mark.asyncio


class FakeClock:
    """A monotonic clock that only moves when the limiter waits."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def waits(clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record requested waits and advance the fake clock by each one.

    Yields to the event loop to allow other tasks to run (necessary for testing
    concurrent wait serialisation).
    """
    recorded: list[float] = []

    async def fake_wait(seconds: float) -> None:
        recorded.append(seconds)
        clock.now += seconds
        await asyncio.sleep(0)

    monkeypatch.setattr(limiter_module, "_wait", fake_wait)
    return recorded


async def test_a_full_bucket_admits_the_whole_burst_without_waiting(clock: FakeClock, waits: list[float]) -> None:
    """The burst equals the concurrency budget, so today's fan-out pays nothing."""
    limiter = RateLimiter(rate=5.0, burst=10, clock=clock)

    for _ in range(10):
        await limiter.acquire()

    assert waits == []
    assert clock.now == 0.0


async def test_the_first_request_past_the_burst_waits_for_one_token(clock: FakeClock, waits: list[float]) -> None:
    """Criterion 1: the eleventh request waits exactly one token's worth of time."""
    limiter = RateLimiter(rate=5.0, burst=10, clock=clock)
    for _ in range(10):
        await limiter.acquire()

    await limiter.acquire()

    assert waits == [pytest.approx(0.2)]
    assert clock.now == pytest.approx(0.2)


async def test_an_idle_bucket_never_overfills(clock: FakeClock, waits: list[float]) -> None:
    """A long idle period buys back the burst, not an unbounded credit."""
    limiter = RateLimiter(rate=5.0, burst=10, clock=clock)
    clock.now = 1_000.0

    for _ in range(10):
        await limiter.acquire()
    await limiter.acquire()

    assert waits == [pytest.approx(0.2)]


async def test_a_paused_limiter_holds_back_every_request(clock: FakeClock, waits: list[float]) -> None:
    """Criterion 4: the server asked for quiet, so nobody goes out before the window ends.

    The bucket is full, so without the pause all three would be admitted at t=0.
    """
    limiter = RateLimiter(rate=5.0, burst=10, clock=clock)
    admitted: list[float] = []

    async def one_request() -> None:
        await limiter.acquire()
        admitted.append(clock.now)

    limiter.pause_for(30.0)
    await asyncio.gather(one_request(), one_request(), one_request())

    assert admitted == [pytest.approx(30.0)] * 3


async def test_the_pause_expires_into_the_nominal_rate(clock: FakeClock, waits: list[float]) -> None:
    """Criterion 5: no lasting degradation — D3's whole argument in one assertion."""
    limiter = RateLimiter(rate=5.0, burst=10, clock=clock)
    limiter.pause_for(30.0)

    for _ in range(10):
        await limiter.acquire()
    assert clock.now == pytest.approx(30.0)

    await limiter.acquire()

    assert clock.now == pytest.approx(30.2)


async def test_a_shorter_pause_never_shortens_a_longer_one(clock: FakeClock, waits: list[float]) -> None:
    """Two concurrent 429s must not let the second one cut the first one's window."""
    limiter = RateLimiter(rate=5.0, burst=10, clock=clock)

    limiter.pause_for(30.0)
    limiter.pause_for(5.0)
    await limiter.acquire()

    assert clock.now == pytest.approx(30.0)


async def test_a_non_positive_pause_is_ignored(clock: FakeClock, waits: list[float]) -> None:
    """`Retry-After: 0` is a valid header and must not become a pause of the past."""
    limiter = RateLimiter(rate=5.0, burst=10, clock=clock)

    limiter.pause_for(0.0)
    await limiter.acquire()

    assert waits == []


async def test_a_pause_past_the_cap_is_clamped_to_max_pause(clock: FakeClock, waits: list[float]) -> None:
    """The limiter owns the bound on what one server signal can cost every caller.

    Before, `pause_for` took any length at face value and relied on its one caller to
    clamp first; a second caller — or a test — could wedge the whole client for hours.
    """
    limiter = RateLimiter(rate=5.0, burst=10, clock=clock, max_pause=60.0)

    limiter.pause_for(10_000.0)
    await limiter.acquire()

    assert clock.now == pytest.approx(60.0)
    assert waits == [pytest.approx(60.0)]


async def test_a_clamped_pause_is_logged(clock: FakeClock, caplog: pytest.LogCaptureFixture) -> None:
    """Silently shortening a server's request for quiet would hide the very signal an operator tunes for."""
    limiter = RateLimiter(rate=5.0, burst=10, clock=clock, max_pause=60.0)

    with caplog.at_level(logging.WARNING, logger="law_scrapper_mcp"):
        limiter.pause_for(10_000.0)

    assert any("exceeds the 60.0s cap" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("max_pause", [0.0, -1.0])
async def test_a_non_positive_max_pause_is_rejected(max_pause: float) -> None:
    """A zero cap would silently turn every server pause into no pause at all."""
    with pytest.raises(ValueError):
        RateLimiter(rate=5.0, burst=10, max_pause=max_pause)


@pytest.mark.parametrize("rate", [0.0, -1.0])
async def test_a_non_positive_rate_is_rejected(rate: float) -> None:
    """A zero rate would wedge every caller on a bucket that never refills."""
    with pytest.raises(ValueError):
        RateLimiter(rate=rate, burst=10)


@pytest.mark.parametrize("burst", [0, -1])
async def test_a_burst_below_one_is_rejected(burst: int) -> None:
    """A bucket that cannot hold a whole token never hands one out."""
    with pytest.raises(ValueError):
        RateLimiter(rate=5.0, burst=burst)


async def test_concurrent_waiters_are_serialised_in_arrival_order(clock: FakeClock, waits: list[float]) -> None:
    """Lock held across waits serialises multiple waiters; no thundering herd."""
    limiter = RateLimiter(rate=5.0, burst=1, clock=clock)
    admitted: list[tuple[int, float]] = []

    async def one_request(task_id: int) -> None:
        await limiter.acquire()
        admitted.append((task_id, clock.now))

    # Drain the single token.
    await limiter.acquire()

    # Three concurrent acquire calls must be admitted in strict arrival order at
    # 0.2/0.4/0.6 s. Without the lock held across the wait, tasks could be admitted
    # out of order (e.g., task 1 at 0.2, task 0 at 0.4, task 2 at 0.6).
    await asyncio.gather(one_request(0), one_request(1), one_request(2))

    assert admitted == [
        (0, pytest.approx(0.2)),
        (1, pytest.approx(0.4)),
        (2, pytest.approx(0.6)),
    ]


async def test_a_pause_does_not_earn_tokens_while_it_lasts(clock: FakeClock, waits: list[float]) -> None:
    """The quiet window must not be repaid as a burst the moment it lifts.

    The companion of `test_the_pause_expires_into_the_nominal_rate`, which starts from a
    full bucket and therefore cannot see this: there the burst is D1's happy path, here
    it would be exactly the herd D2 exists to prevent, aimed at a server that just asked
    for quiet. The bucket is drained first because that is the state the traffic which
    earned the 429 would have left behind.
    """
    limiter = RateLimiter(rate=5.0, burst=10, clock=clock)
    for _ in range(10):
        await limiter.acquire()

    limiter.pause_for(30.0)
    admitted: list[float] = []

    async def one_request() -> None:
        await limiter.acquire()
        admitted.append(clock.now)

    await asyncio.gather(one_request(), one_request(), one_request())

    assert admitted == [pytest.approx(30.2), pytest.approx(30.4), pytest.approx(30.6)]


async def test_a_pause_longer_than_the_deadline_is_refused_without_waiting(
    clock: FakeClock, waits: list[float]
) -> None:
    """Pacing must not outlive the budget of the operation being paced.

    Sitting the wait out and failing afterwards is strictly worse than failing now: it
    holds a circuit-breaker probe slot for the whole window.
    """
    limiter = RateLimiter(rate=5.0, burst=10, clock=clock)
    limiter.pause_for(30.0)

    with pytest.raises(EgressPaceDeadlineError) as refused:
        await limiter.acquire(deadline=clock.now + 5.0)

    assert refused.value.wait == pytest.approx(30.0)
    # The cause travels with the error: only this path is a halt, and the message an
    # agent reads is picked from it.
    assert refused.value.paused is True
    assert waits == []
    assert clock.now == 0.0


async def test_a_wait_that_fits_the_deadline_is_still_taken(clock: FakeClock, waits: list[float]) -> None:
    """The bound refuses what does not fit, not everything."""
    limiter = RateLimiter(rate=5.0, burst=10, clock=clock)
    limiter.pause_for(30.0)

    await limiter.acquire(deadline=clock.now + 45.0)

    assert clock.now == pytest.approx(30.0)


async def test_a_token_wait_past_the_deadline_is_refused(clock: FakeClock, waits: list[float]) -> None:
    """Not only pauses: an ordinary queue of waiters can outlast a budget too."""
    limiter = RateLimiter(rate=5.0, burst=10, clock=clock)
    for _ in range(10):
        await limiter.acquire()

    with pytest.raises(EgressPaceDeadlineError) as refused:
        await limiter.acquire(deadline=clock.now + 0.1)

    # Nothing is halted here — this is ordinary queueing, and the agent is told so.
    assert refused.value.paused is False
    assert waits == []


async def test_a_deadline_never_refuses_a_token_that_is_already_there(clock: FakeClock, waits: list[float]) -> None:
    """The bound gates waits, not admissions — the happy path pays nothing for it."""
    limiter = RateLimiter(rate=5.0, burst=10, clock=clock)

    await limiter.acquire(deadline=clock.now)

    assert waits == []
