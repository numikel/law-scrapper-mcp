"""Pace and fairness of outbound traffic as wired into the client (F27, F55).

Runs offline on respx. Waiting is injected in both places that wait — the limiter's
`_wait` and the retry loop's `_delay` — and both advance the same fake clock, so no
test measures real elapsed time (design constraint 3).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
import respx

from law_scrapper_mcp.client import rate_limiter as limiter_module
from law_scrapper_mcp.client import sejm_client as client_module
from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.exceptions import SejmApiError
from law_scrapper_mcp.client.rate_limiter import RateLimiter
from law_scrapper_mcp.client.sejm_client import RequestClass, SejmApiClient
from law_scrapper_mcp.config import Settings

pytestmark = pytest.mark.asyncio

ACT_URL = "https://api.sejm.gov.pl/eli/acts/DU/2024/1"
ACT_PATH = "acts/DU/2024/1"
PDF_URL = "https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.pdf"
PDF_PATH = "acts/DU/2024/1/text.pdf"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def waits(clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Both waiting points advance one shared fake clock."""
    recorded: list[float] = []

    async def fake_wait(seconds: float) -> None:
        recorded.append(seconds)
        clock.now += seconds

    monkeypatch.setattr(limiter_module, "_wait", fake_wait)
    monkeypatch.setattr(client_module, "_delay", fake_wait)
    return recorded


@pytest_asyncio.fixture
async def paced_client(clock: FakeClock) -> AsyncGenerator[SejmApiClient]:
    api = SejmApiClient(
        cache=TTLCache(max_entries=100),
        rate_limiter=RateLimiter(rate=5.0, burst=10, clock=clock),
    )
    await api.start()
    yield api
    await api.close()


@respx.mock
async def test_fifty_sequential_calls_cost_the_nominal_time(
    paced_client: SejmApiClient, clock: FakeClock, waits: list[float]
) -> None:
    """Criterion 2: (50 - 10) / 5 = 8.0 s of pacing, on the injected clock."""
    respx.get(ACT_URL).mock(return_value=httpx.Response(200, json={"ELI": "DU/2024/1"}))

    for _ in range(50):
        await paced_client.get_json(ACT_PATH)

    assert clock.now >= 8.0
    assert sum(waits) == pytest.approx(8.0)


@respx.mock
async def test_a_429_pauses_the_whole_client(
    clock: FakeClock, waits: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 4, client half: the server's signal reaches the shared limiter.

    That the pause then holds every waiting request is proven one layer down, in
    `test_a_paused_limiter_holds_back_every_request`.
    """
    paused: list[float] = []
    limiter = RateLimiter(rate=5.0, burst=10, clock=clock)
    monkeypatch.setattr(limiter, "pause_for", paused.append)

    api = SejmApiClient(cache=TTLCache(max_entries=100), rate_limiter=limiter)
    await api.start()
    respx.get(ACT_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "30"}),
            httpx.Response(200, json={"ELI": "DU/2024/1"}),
        ]
    )
    try:
        await api.get_json(ACT_PATH)
    finally:
        await api.close()

    assert paused == [pytest.approx(30.0)]


@respx.mock
async def test_a_pause_below_the_cap_is_still_logged(
    clock: FakeClock, waits: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    """A 55 s freeze of every other caller must not be silent just because it fits under the cap."""
    limiter = RateLimiter(rate=5.0, burst=10, clock=clock)

    api = SejmApiClient(cache=TTLCache(max_entries=100), rate_limiter=limiter)
    await api.start()
    respx.get(ACT_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "30"}),
            httpx.Response(200, json={"ELI": "DU/2024/1"}),
        ]
    )
    try:
        with caplog.at_level(logging.WARNING, logger="law_scrapper_mcp"):
            await api.get_json(ACT_PATH)
    finally:
        await api.close()

    assert any("Pausing egress for" in r.getMessage() for r in caplog.records)


@respx.mock
async def test_a_429_pauses_the_client_even_when_the_request_gives_up(
    clock: FakeClock, waits: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abandoning one request must not discard the server's request for quiet."""
    paused: list[float] = []
    limiter = RateLimiter(rate=5.0, burst=10, clock=clock)
    monkeypatch.setattr(limiter, "pause_for", paused.append)

    api = SejmApiClient(cache=TTLCache(max_entries=100), rate_limiter=limiter, max_attempts=1)
    await api.start()
    respx.get(ACT_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "30"}))
    try:
        with pytest.raises(SejmApiError):
            await api.get_json(ACT_PATH)
    finally:
        await api.close()

    assert paused == [pytest.approx(30.0)]


@respx.mock
async def test_a_retry_after_far_above_the_cap_is_clamped_but_retry_policy_is_not(
    clock: FakeClock, waits: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unbounded Retry-After would wedge every other caller for its full length.

    The clamp lives only at the `pause_for` call site: `delay`/`give_up` still see the
    real, unclamped header value, so this same oversized response still ends the
    request immediately (max_attempts=1, no retry sleep) instead of the clamp
    silently turning "give up now" into "retry after 60s".
    """
    paused: list[float] = []
    limiter = RateLimiter(rate=5.0, burst=10, clock=clock)
    monkeypatch.setattr(limiter, "pause_for", paused.append)

    api = SejmApiClient(cache=TTLCache(max_entries=100), rate_limiter=limiter, max_attempts=1)
    await api.start()
    route = respx.get(ACT_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "3600"}))
    try:
        with pytest.raises(SejmApiError):
            await api.get_json(ACT_PATH)
    finally:
        await api.close()

    assert paused == [pytest.approx(client_module.MAX_SERVER_PAUSE)]
    assert route.call_count == 1
    assert waits == []


@respx.mock
async def test_heavy_downloads_cannot_starve_the_light_lane() -> None:
    """Criterion 13: two PDFs hold both heavy slots, a third waits, JSON still passes."""
    gate = asyncio.Event()
    in_flight = 0
    peak = 0

    async def slow_pdf(_request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await gate.wait()
        in_flight -= 1
        return httpx.Response(200, content=b"%PDF-1.4")

    respx.get(PDF_URL).mock(side_effect=slow_pdf)
    respx.get(ACT_URL).mock(return_value=httpx.Response(200, json={"ELI": "DU/2024/1"}))

    api = SejmApiClient(cache=TTLCache(max_entries=100), max_concurrent=8, max_concurrent_content=2)
    await api.start()
    try:
        downloads = [asyncio.create_task(api.get_bytes(PDF_PATH)) for _ in range(3)]
        for _ in range(20):
            await asyncio.sleep(0)

        assert in_flight == 2, "the third download must wait for a heavy slot"

        light = await api.get_json(ACT_PATH)
        assert light == {"ELI": "DU/2024/1"}

        gate.set()
        assert len(await asyncio.gather(*downloads)) == 3
        assert peak == 2
    finally:
        await api.close()


async def test_the_two_lanes_sum_to_the_previous_peak() -> None:
    """Criterion 14: the split rebalances the peak the API sees, it does not raise it."""
    settings = Settings()
    api = SejmApiClient(
        cache=TTLCache(max_entries=1),
        max_concurrent=settings.api_max_concurrent,
        max_concurrent_content=settings.api_max_concurrent_content,
    )

    # Reading `_value` is deliberate: capacity at rest is exactly what this asserts.
    light = api._semaphores[RequestClass.LIGHT]._value
    heavy = api._semaphores[RequestClass.HEAVY]._value

    assert (light, heavy) == (8, 2)
    assert light + heavy == 10


@respx.mock
async def test_the_token_is_taken_before_the_concurrency_slot() -> None:
    """Spec risk 4: a request waiting for a token must not sit on a concurrency slot.

    With the order reversed, rate limiting silently degrades into a second, slower
    concurrency limit. The spy reads the semaphore at the moment the token is asked
    for: still full means the slot has not been taken yet.

    No fake clock here on purpose: a single request against a fresh 10-token burst
    never waits, so there is no delay to control — wiring in `FakeClock` without also
    patching `_wait` (as the `waits` fixture does) would leave the limiter's real
    `asyncio.sleep` reading a clock nothing advances, a hang trap if this test ever
    grew a second request.
    """
    respx.get(ACT_URL).mock(return_value=httpx.Response(200, json={"ELI": "DU/2024/1"}))
    observed: list[int] = []

    class OrderSpy(RateLimiter):
        async def acquire(self) -> None:
            observed.append(api._semaphores[RequestClass.LIGHT]._value)
            await super().acquire()

    api = SejmApiClient(
        cache=TTLCache(max_entries=100),
        max_concurrent=3,
        rate_limiter=OrderSpy(rate=5.0, burst=10),
    )
    await api.start()
    try:
        await api.get_json(ACT_PATH)
    finally:
        await api.close()

    assert observed == [3]
