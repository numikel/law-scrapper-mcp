"""Golden texts of the errors the Sejm client raises.

The Sejm client must not change behaviour when the transport is extracted into a host-agnostic
client, and these messages are what an agent reads. Only three older test files mention any of
these strings, so each one is pinned here as a literal, written against the code as it was
before the extraction. The expected strings are deliberately not built from any message table.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
import respx

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.circuit_breaker import CircuitBreaker
from law_scrapper_mcp.client.exceptions import ApiUnavailableError, SejmApiError
from law_scrapper_mcp.client.rate_limiter import RateLimiter
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from test_client.conftest import FakeClock

ACT_URL = "https://api.sejm.gov.pl/eli/acts/DU/2024/1"
ACT_PATH = "acts/DU/2024/1"


@pytest_asyncio.fixture
async def client(waits: list[float]) -> AsyncGenerator[SejmApiClient]:
    """One attempt per operation, so the first failure is the one that surfaces."""
    api = SejmApiClient(cache=TTLCache(max_entries=10), max_attempts=1)
    await api.start()
    yield api
    await api.close()


@pytest.mark.asyncio
@respx.mock
async def test_a_5xx_status_message(client: SejmApiClient) -> None:
    respx.get(ACT_URL).mock(return_value=httpx.Response(503))

    with pytest.raises(ApiUnavailableError) as excinfo:
        await client.get_json(ACT_PATH)

    assert str(excinfo.value) == "API Sejmu chwilowo niedostępne (HTTP 503)"
    assert excinfo.value.status_code == 503
    assert excinfo.value.url == ACT_URL


@pytest.mark.asyncio
@respx.mock
async def test_a_connection_failure_message(client: SejmApiClient) -> None:
    respx.get(ACT_URL).mock(side_effect=httpx.ConnectError("[Errno 111] refused"))

    with pytest.raises(ApiUnavailableError) as excinfo:
        await client.get_json(ACT_PATH)

    assert str(excinfo.value) == "Błąd połączenia z API Sejmu (ConnectError) podczas żądania GET acts/DU/2024/1"


@pytest.mark.asyncio
@respx.mock
async def test_a_client_error_status_keeps_its_body_in_the_message(client: SejmApiClient) -> None:
    """The body is in the exception text; the tool layer redacts it, the client does not."""
    respx.get(ACT_URL).mock(return_value=httpx.Response(418, text="tea"))

    with pytest.raises(SejmApiError) as excinfo:
        await client.get_json(ACT_PATH)

    assert type(excinfo.value) is SejmApiError
    assert str(excinfo.value) == "HTTP 418: tea"
    assert excinfo.value.status_code == 418


@pytest.mark.asyncio
@respx.mock
async def test_a_non_transport_httpx_error_message(client: SejmApiClient) -> None:
    respx.get(ACT_URL).mock(side_effect=httpx.DecodingError("zły kodek"))

    with pytest.raises(SejmApiError) as excinfo:
        await client.get_json(ACT_PATH)

    assert type(excinfo.value) is SejmApiError
    assert str(excinfo.value) == "Błędne żądanie do API Sejmu: zły kodek"


@pytest.mark.asyncio
@respx.mock
async def test_an_open_breaker_message() -> None:
    breaker = CircuitBreaker(failure_threshold=1)
    api = SejmApiClient(cache=TTLCache(max_entries=10), circuit_breaker=breaker, max_attempts=1)
    await api.start()
    respx.get(ACT_URL).mock(return_value=httpx.Response(500))
    try:
        with pytest.raises(ApiUnavailableError):
            await api.get_json(ACT_PATH)

        with pytest.raises(ApiUnavailableError) as excinfo:
            await api.get_json(ACT_PATH)
    finally:
        await api.close()

    assert str(excinfo.value) == "API Sejmu tymczasowo niedostępne (bezpiecznik otwarty)"
    assert excinfo.value.status_code == 503


@pytest.mark.asyncio
@respx.mock
async def test_a_server_requested_pause_message(clock: FakeClock, waits: list[float]) -> None:
    limiter = RateLimiter(rate=5.0, burst=10, clock=clock)
    api = SejmApiClient(cache=TTLCache(max_entries=10), rate_limiter=limiter, retry_budget=5.0)
    await api.start()
    respx.get(ACT_URL).mock(return_value=httpx.Response(200, json={}))
    try:
        limiter.pause_for(60.0)
        with pytest.raises(ApiUnavailableError) as excinfo:
            await api.get_json(ACT_PATH)
    finally:
        await api.close()

    assert str(excinfo.value) == (
        "Ruch do API Sejmu jest wstrzymany na prośbę serwera (~60 s) — "
        "nie zmieściłoby się w budżecie czasu operacji. Spróbuj ponownie za chwilę."
    )
    assert excinfo.value.status_code == 503


@pytest.mark.asyncio
@respx.mock
async def test_a_pacing_queue_message(clock: FakeClock, waits: list[float]) -> None:
    limiter = RateLimiter(rate=1.0, burst=1, clock=clock)
    api = SejmApiClient(cache=TTLCache(max_entries=10), rate_limiter=limiter, retry_budget=0.5)
    await api.start()
    respx.get(ACT_URL).mock(return_value=httpx.Response(200, json={}))
    try:
        await api.get_json(ACT_PATH)  # spends the only token
        with pytest.raises(ApiUnavailableError) as excinfo:
            await api.get_json(ACT_PATH)
    finally:
        await api.close()

    assert str(excinfo.value) == (
        "Żądanie musiałoby czekać w kolejce na przepustowość (~1 s) — "
        "nie zmieściłoby się w budżecie czasu operacji. Spróbuj ponownie za chwilę."
    )
