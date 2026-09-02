"""Shared clock for the egress suites (`test_rate_limiter`, `test_egress_pacing`).

The limiter and the retry loop each have exactly one waiting point — `rate_limiter._wait`
and `sejm_client._delay`. Both are swapped here for a wait that advances one injected
clock instead of sleeping, so no test in this package measures real elapsed time
(design constraint 3). One definition rather than one per module: the two copies had
already drifted apart in which wait point they patched and whether they yielded to the
loop, and a test written against the weaker copy would silently pass under the other.
"""

from __future__ import annotations

import asyncio

import pytest

from law_scrapper_mcp.client import rate_limiter as limiter_module
from law_scrapper_mcp.client import sejm_client as client_module


class FakeClock:
    """A monotonic clock that only moves when something waits on it.

    `now` is public on purpose: tests set it directly to model an idle period or a
    starting magnitude, and read it to assert when a request was admitted.
    """

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def waits(clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record every requested wait and advance the shared clock by it.

    Patches both waiting points — the limiter's `_wait` and the retry loop's `_delay` —
    so a client wired to a limiter sees one timeline. Each fake wait also yields to the
    event loop once: a wait that never suspends would let a single task run the whole
    scenario without any other task getting a turn, hiding every ordering property
    (arrival-order admission, a pause extended by another task mid-wait, cancellation
    while parked) that the limiter's lock exists to provide.
    """
    recorded: list[float] = []

    async def fake_wait(seconds: float) -> None:
        recorded.append(seconds)
        clock.now += seconds
        await asyncio.sleep(0)

    monkeypatch.setattr(limiter_module, "_wait", fake_wait)
    monkeypatch.setattr(client_module, "_delay", fake_wait)
    return recorded
