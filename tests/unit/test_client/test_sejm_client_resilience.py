"""Sejm API client resilience tests (F10, F11, F19, F20; criteria 5.1-5.4, 5.7).

The whole suite runs offline on respx, and waiting is injected by substituting
`_delay` — no test measures real elapsed time (O6).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
import respx

from law_scrapper_mcp.client import sejm_client as client_module
from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.circuit_breaker import CircuitBreaker, CircuitState
from law_scrapper_mcp.client.exceptions import (
    ActNotFoundError,
    ApiUnavailableError,
    SejmApiError,
)
from law_scrapper_mcp.client.sejm_client import SejmApiClient

ACT_URL = "https://api.sejm.gov.pl/eli/acts/DU/2024/1"
ACT_PATH = "acts/DU/2024/1"


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Capture the requested delays instead of sitting them out."""
    recorded: list[float] = []

    async def fake_delay(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(client_module, "_delay", fake_delay)
    return recorded


@pytest.fixture
def breaker() -> CircuitBreaker:
    return CircuitBreaker(failure_threshold=5, recovery_timeout=60.0, half_open_max_calls=3)


@pytest_asyncio.fixture
async def client(breaker: CircuitBreaker) -> AsyncGenerator[SejmApiClient]:
    api = SejmApiClient(
        cache=TTLCache(max_entries=100),
        timeout=30.0,
        max_concurrent=10,
        circuit_breaker=breaker,
        max_attempts=3,
        retry_budget=45.0,
    )
    await api.start()
    yield api
    await api.close()


@pytest.mark.asyncio
@respx.mock
async def test_503_is_retried_until_success(client: SejmApiClient, slept: list[float]) -> None:
    """F10, criterion 5.1: two 503s then a 200 — three requests, data comes back."""
    route = respx.get(ACT_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, json={"ELI": "DU/2024/1"}),
        ]
    )

    data = await client.get_json(ACT_PATH)

    assert data == {"ELI": "DU/2024/1"}
    assert route.call_count == 3
    assert slept == [1.0, 2.0]


@pytest.mark.asyncio
@respx.mock
async def test_500_exhausts_attempts_then_raises(client: SejmApiClient, slept: list[float]) -> None:
    """F10 + D7, criterion 5.1: 500 is retried and ends as ApiUnavailableError."""
    route = respx.get(ACT_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(ApiUnavailableError):
        await client.get_json(ACT_PATH)

    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_connect_error_is_retried(client: SejmApiClient, slept: list[float]) -> None:
    """F11, criterion 5.2: a transport error on attempt one, success on attempt two."""
    route = respx.get(ACT_URL).mock(
        side_effect=[
            httpx.ConnectError("brak połączenia"),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    assert await client.get_json(ACT_PATH) == {"ok": True}
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_read_error_surfaces_as_domain_exception(client: SejmApiClient, slept: list[float]) -> None:
    """F11, criterion 5.2: the caller never sees a raw httpx exception."""
    respx.get(ACT_URL).mock(side_effect=httpx.ReadError("błąd odczytu"))

    with pytest.raises(ApiUnavailableError):
        await client.get_json(ACT_PATH)


@pytest.mark.asyncio
@respx.mock
async def test_repeated_transport_error_counts_one_failure(
    client: SejmApiClient, breaker: CircuitBreaker, slept: list[float]
) -> None:
    """F11 + F20, criteria 5.2 and 5.4: three attempts, exactly one breaker failure."""
    respx.get(ACT_URL).mock(side_effect=httpx.ConnectError("brak połączenia"))

    with pytest.raises(ApiUnavailableError):
        await client.get_json(ACT_PATH)

    assert breaker.failure_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_five_failed_operations_open_the_circuit(
    client: SejmApiClient, breaker: CircuitBreaker, slept: list[float]
) -> None:
    """F19 + F20, criteria 5.3 and 5.4: a threshold of 5 means five operations, not two."""
    route = respx.get(ACT_URL).mock(return_value=httpx.Response(500))

    for _ in range(4):
        with pytest.raises(ApiUnavailableError):
            await client.get_json(ACT_PATH)
    assert breaker.state is CircuitState.CLOSED
    assert breaker.failure_count == 4

    with pytest.raises(ApiUnavailableError):
        await client.get_json(ACT_PATH)
    assert breaker.state is CircuitState.OPEN

    calls_before = route.call_count
    with pytest.raises(ApiUnavailableError):
        await client.get_json(ACT_PATH)
    assert route.call_count == calls_before


@pytest.mark.asyncio
@respx.mock
async def test_budget_cuts_the_retry_sequence(
    breaker: CircuitBreaker, slept: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """F20, criterion 5.4: an exceeded budget cuts the attempts instead of sleeping."""
    route = respx.get(ACT_URL).mock(return_value=httpx.Response(503))
    api = SejmApiClient(
        cache=TTLCache(max_entries=100),
        timeout=30.0,
        max_concurrent=10,
        circuit_breaker=breaker,
        max_attempts=3,
        retry_budget=0.5,
    )
    await api.start()
    try:
        with pytest.raises(ApiUnavailableError):
            await api.get_json(ACT_PATH)
    finally:
        await api.close()

    assert route.call_count == 1
    assert slept == []


@pytest.mark.asyncio
@respx.mock
async def test_budget_is_never_exceeded_by_planned_sleeps(client: SejmApiClient, slept: list[float]) -> None:
    """F20, criterion 5.4: the planned waits sum to less than the budget."""
    respx.get(ACT_URL).mock(return_value=httpx.Response(503))

    with pytest.raises(ApiUnavailableError):
        await client.get_json(ACT_PATH)

    assert sum(slept) < 45.0


@pytest.mark.asyncio
@respx.mock
async def test_read_timeout_is_cut_by_the_budget(breaker: CircuitBreaker, slept: list[float]) -> None:
    """F20, criterion 5.4c: a repeated read timeout ends within the budget.

    The other budget tests drive HTTP 503. This one drives an actual
    `httpx.ReadTimeout` — the failure mode criterion 5.4c names, and the one whose
    30 s read timeout is the reason the budget exists at all.
    """
    route = respx.get(ACT_URL).mock(side_effect=httpx.ReadTimeout("przekroczono czas odczytu"))
    api = SejmApiClient(
        cache=TTLCache(max_entries=100),
        timeout=30.0,
        max_concurrent=10,
        circuit_breaker=breaker,
        max_attempts=3,
        retry_budget=0.5,
    )
    await api.start()
    try:
        with pytest.raises(ApiUnavailableError):
            await api.get_json(ACT_PATH)
    finally:
        await api.close()

    assert route.call_count == 1
    assert slept == []
    assert breaker.failure_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_circuit_opened_mid_sequence_aborts_retries(
    client: SejmApiClient, breaker: CircuitBreaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D2, criterion 5.4: a circuit opened by concurrent traffic aborts our sequence.

    We swap `_delay` for one that, while waiting, simulates concurrent traffic
    driving the breaker to its threshold — the truest rendering of the race D2
    describes, without relying on task ordering.
    """
    route = respx.get(ACT_URL).mock(return_value=httpx.Response(503))

    async def open_the_circuit(_seconds: float) -> None:
        for _ in range(5):
            breaker.release_failure()

    monkeypatch.setattr(client_module, "_delay", open_the_circuit)

    with pytest.raises(ApiUnavailableError):
        await client.get_json(ACT_PATH)

    assert breaker.state is CircuitState.OPEN
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_half_open_admits_exactly_max_calls(slept: list[float]) -> None:
    """F21, criterion 5.5: ten concurrent operations, three reach the API.

    A breaker with a zero recovery timeout enters HALF_OPEN on the first
    `try_acquire()`, so the test manipulates neither the clock nor a private field.
    """
    breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=0.0, half_open_max_calls=3)
    api = SejmApiClient(
        cache=TTLCache(max_entries=100),
        timeout=30.0,
        max_concurrent=10,
        circuit_breaker=breaker,
        max_attempts=3,
        retry_budget=45.0,
    )
    await api.start()

    for _ in range(5):
        breaker.release_failure()
    assert breaker.state is CircuitState.OPEN

    async def respond(request: httpx.Request) -> httpx.Response:
        # A suspension point is required here: without it respx answers fully
        # synchronously, so `asyncio.gather` runs the ten tasks one after another
        # (each to completion) before starting the next — zero real concurrency,
        # and the HALF_OPEN limit is never put under pressure.
        await asyncio.sleep(0)
        return httpx.Response(200, json={"ok": True})

    route = respx.get(ACT_URL).mock(side_effect=respond)
    try:
        results = await asyncio.gather(
            *(api.get_json(ACT_PATH) for _ in range(10)),
            return_exceptions=True,
        )
    finally:
        await api.close()

    admitted = [item for item in results if not isinstance(item, BaseException)]
    rejected = [item for item in results if isinstance(item, ApiUnavailableError)]
    assert len(admitted) == 3
    assert len(rejected) == 7
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_429_retries_once_honouring_retry_after(
    client: SejmApiClient, breaker: CircuitBreaker, slept: list[float]
) -> None:
    """D6, criterion 5.7: one retry after the header delay, no breaker failure."""
    route = respx.get(ACT_URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "2"}),
    )

    with pytest.raises(SejmApiError) as caught:
        await client.get_json(ACT_PATH)

    assert not isinstance(caught.value, ApiUnavailableError)
    assert route.call_count == 2
    assert slept == [2.0]
    assert breaker.failure_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_404_is_terminal_and_invisible_to_the_breaker(
    client: SejmApiClient, breaker: CircuitBreaker, slept: list[float]
) -> None:
    """Criterion 5.7: 404 still raises ActNotFoundError, with no retry and no failure."""
    route = respx.get(ACT_URL).mock(return_value=httpx.Response(404))

    with pytest.raises(ActNotFoundError):
        await client.get_json(ACT_PATH)

    assert route.call_count == 1
    assert breaker.failure_count == 0
    assert slept == []


@pytest.mark.asyncio
@respx.mock
async def test_client_error_stays_a_generic_api_error(client: SejmApiClient, slept: list[float]) -> None:
    """O3 + D7: a 4xx other than 404 is not promoted to ApiUnavailableError."""
    route = respx.get(ACT_URL).mock(return_value=httpx.Response(400, text="złe zapytanie"))

    with pytest.raises(SejmApiError) as caught:
        await client.get_json(ACT_PATH)

    assert not isinstance(caught.value, ApiUnavailableError)
    assert caught.value.status_code == 400
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_open_circuit_rejects_before_sending(
    client: SejmApiClient, breaker: CircuitBreaker, slept: list[float]
) -> None:
    """An open circuit rejects the request with no outbound traffic (O1)."""
    for _ in range(5):
        breaker.release_failure()
    route = respx.get(ACT_URL).mock(return_value=httpx.Response(200, json={"ok": True}))

    with pytest.raises(ApiUnavailableError):
        await client.get_json(ACT_PATH)

    assert route.call_count == 0


@pytest.mark.asyncio
async def test_cancelled_probe_releases_the_half_open_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix (Critical): cancelling a request must not wedge the breaker permanently.

    `asyncio.CancelledError` is not an `httpx` exception, so it bypasses policy
    classification (`classify_failure` only accepts `httpx.HTTPError`). Without
    dedicated handling the HALF_OPEN slot never returns to zero, and the breaker
    can no longer reach `release_failure()` — the only route back to OPEN — so it
    rejects everything forever.
    """
    breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=0.0, half_open_max_calls=3)
    api = SejmApiClient(
        cache=TTLCache(max_entries=100),
        timeout=30.0,
        max_concurrent=10,
        circuit_breaker=breaker,
        max_attempts=3,
        retry_budget=45.0,
    )
    await api.start()

    for _ in range(5):
        breaker.release_failure()
    assert breaker.state is CircuitState.OPEN

    async def cancelled_send(*args: object, **kwargs: object) -> httpx.Response:
        raise asyncio.CancelledError()

    monkeypatch.setattr(api, "_send", cancelled_send)

    try:
        with pytest.raises(asyncio.CancelledError):
            await api.get_json(ACT_PATH)
    finally:
        await api.close()

    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.try_acquire() is True
    assert breaker.try_acquire() is True
    assert breaker.try_acquire() is True


@pytest.mark.asyncio
@respx.mock
async def test_breaker_counts_failure_seen_before_a_trailing_429(
    client: SejmApiClient, breaker: CircuitBreaker, slept: list[float]
) -> None:
    """Fix (Important): an earlier 5xx must not vanish behind a trailing 429.

    `give_up` looked only at the verdict of the last attempt — without a latch
    spanning all attempts, a 500 -> 500 -> 429 sequence would release only the
    probe despite two confirmed server failures, so the breaker would never open
    against an API that degrades in exactly this way.
    """
    route = respx.get(ACT_URL).mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(500),
            httpx.Response(429, headers={"Retry-After": "1"}),
        ]
    )

    with pytest.raises(SejmApiError) as caught:
        await client.get_json(ACT_PATH)

    assert not isinstance(caught.value, ApiUnavailableError)
    assert route.call_count == 3
    assert slept == [1.0, 2.0]
    assert breaker.failure_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_failure_survives_admission_refusal_mid_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix (Critical): a failure confirmed before admission is refused must not vanish.

    In HALF_OPEN our probe gets a 500, frees its slot for the wait, and on the next
    attempt finds the slot taken by concurrent traffic. Without booking the latched
    failure the breaker stays HALF_OPEN, so an API confirmed broken can be declared
    recovered by the probes that happened to succeed.
    """
    breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=0.0, half_open_max_calls=1)
    api = SejmApiClient(
        cache=TTLCache(max_entries=100),
        timeout=30.0,
        max_concurrent=10,
        circuit_breaker=breaker,
        max_attempts=3,
        retry_budget=45.0,
    )
    await api.start()

    for _ in range(5):
        breaker.release_failure()
    assert breaker.state is CircuitState.OPEN

    route = respx.get(ACT_URL).mock(return_value=httpx.Response(500))

    async def steal_the_only_slot(_seconds: float) -> None:
        assert breaker.try_acquire() is True

    monkeypatch.setattr(client_module, "_delay", steal_the_only_slot)
    try:
        with pytest.raises(ApiUnavailableError):
            await api.get_json(ACT_PATH)
    finally:
        await api.close()

    assert route.call_count == 1
    assert breaker.state is CircuitState.OPEN
    assert breaker.failure_count == 6


def test_settings_expose_retry_budget_and_attempts() -> None:
    """Criterion 4.3: both settings exist with the default values from the spec."""
    from law_scrapper_mcp.config import Settings

    settings = Settings()
    assert settings.api_max_attempts == 3
    assert settings.api_retry_budget == pytest.approx(45.0)


def test_lifespan_wires_client_settings_into_the_client() -> None:
    """The settings must actually reach the client, not merely exist in the config."""
    import inspect

    from law_scrapper_mcp import server

    source = inspect.getsource(server.lifespan)
    assert "max_attempts=settings.api_max_attempts" in source
    assert "retry_budget=settings.api_retry_budget" in source
    assert "max_concurrent=settings.api_max_concurrent" in source
    assert "max_concurrent_content=settings.api_max_concurrent_content" in source
    assert "rate_per_second=settings.api_rate_per_second" in source
    assert "rate_burst=settings.api_rate_burst" in source
    assert "user_agent=settings.user_agent" in source


def test_tenacity_is_not_a_runtime_dependency() -> None:
    """D9: the only consumer is gone, and so is the dependency."""
    import tomllib
    from pathlib import Path

    project_root = Path(__file__).parents[3]
    with (project_root / "pyproject.toml").open("rb") as pyproject:
        dependencies = tomllib.load(pyproject)["project"]["dependencies"]

    assert not any(dependency.lower().startswith("tenacity") for dependency in dependencies)
