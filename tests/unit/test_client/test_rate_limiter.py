"""Outbound token bucket protecting api.sejm.gov.pl from our own pace (finding F27).

Not to be confused with `tests/unit/test_rate_limit.py`, which covers the inbound
limiter in `http/rate_limit.py`. This one is about traffic we send.

Every test drives an injected clock; none sits out a real delay (design constraint 3).
"""

from __future__ import annotations

import asyncio

import pytest

from law_scrapper_mcp.client import rate_limiter as limiter_module
from law_scrapper_mcp.client.rate_limiter import RateLimiter

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
