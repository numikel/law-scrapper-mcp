"""Client-side texts of a host that has no preset.

Expected strings are written out literally, so a wording change is a deliberate edit here (client-side
part only: the "other sources" sentence and the category guidance are added by the tool layer). The
example source is called SAOS.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.circuit_breaker import CircuitBreaker
from law_scrapper_mcp.client.egress import EgressClient, EgressHost, HostMessages
from law_scrapper_mcp.client.exceptions import ApiUnavailableError, EgressRedirectRefusedError, SejmApiError
from law_scrapper_mcp.client.rate_limiter import RateLimiter
from test_client.conftest import FakeClock

BASE = "https://saos.example.test/api"


def make_client(clock: FakeClock, **kwargs: object) -> EgressClient:
    limiter = RateLimiter(rate=1.0, burst=1, max_pause=60.0, clock=clock)
    return EgressClient(
        host=EgressHost(name="SAOS", base_url=BASE),
        cache=TTLCache(max_entries=10),
        max_attempts=1,
        rate_limiter=limiter,
        **kwargs,  # type: ignore[arg-type]
    )


def test_a_host_without_messages_resolves_to_the_generic_wording() -> None:
    client = EgressClient(host=EgressHost(name="SAOS", base_url=BASE), cache=TTLCache(max_entries=10))

    assert client._messages == HostMessages.generic("SAOS")


@pytest.mark.asyncio
@respx.mock
async def test_generic_5xx_text(clock: FakeClock) -> None:
    respx.get(f"{BASE}/search/judgments").mock(return_value=httpx.Response(503))
    client = make_client(clock)
    try:
        with pytest.raises(ApiUnavailableError) as excinfo:
            await client.get_json("search/judgments")
    finally:
        await client.close()

    assert str(excinfo.value) == "Źródło SAOS jest chwilowo niedostępne (HTTP 503)"


@pytest.mark.asyncio
@respx.mock
async def test_generic_connection_failure_text(clock: FakeClock) -> None:
    respx.get(f"{BASE}/search/judgments").mock(side_effect=httpx.ConnectError("refused"))
    client = make_client(clock)
    try:
        with pytest.raises(ApiUnavailableError) as excinfo:
            await client.get_json("search/judgments")
    finally:
        await client.close()

    assert str(excinfo.value) == "Błąd połączenia ze źródłem SAOS (ConnectError) podczas żądania GET search/judgments"


@pytest.mark.asyncio
@respx.mock
async def test_generic_breaker_open_text(clock: FakeClock) -> None:
    breaker = CircuitBreaker(failure_threshold=1)
    client = make_client(clock, circuit_breaker=breaker)
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(503))
    try:
        with pytest.raises(ApiUnavailableError):
            await client.get_json("x")
        with pytest.raises(ApiUnavailableError) as excinfo:
            await client.get_json("x")
    finally:
        await client.close()

    assert str(excinfo.value) == "Źródło SAOS jest tymczasowo niedostępne (bezpiecznik otwarty)"


@pytest.mark.asyncio
@respx.mock
async def test_generic_pacing_texts(clock: FakeClock, waits: list[float]) -> None:
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(200, json={}))
    client = EgressClient(
        host=EgressHost(name="SAOS", base_url=BASE),
        cache=TTLCache(max_entries=10),
        max_attempts=1,
        retry_budget=2.0,
        rate_limiter=RateLimiter(rate=0.1, burst=1, max_pause=60.0, clock=clock),
    )
    try:
        await client.get_json("x")  # spends the only token
        with pytest.raises(ApiUnavailableError) as queued:
            await client.get_json("x")  # next token in ~10 s, budget 2 s
        client._rate_limiter.pause_for(30.0)
        with pytest.raises(ApiUnavailableError) as paused:
            await client.get_json("x")
    finally:
        await client.close()

    assert str(queued.value) == (
        "Żądanie do źródła SAOS musiałoby czekać w kolejce na przepustowość (~10 s) — "
        "nie zmieściłoby się w budżecie czasu operacji"
    )
    assert str(paused.value) == (
        "Ruch do źródła SAOS jest wstrzymany na prośbę serwera (~30 s) — nie zmieściłoby się w budżecie czasu operacji"
    )


@pytest.mark.asyncio
@respx.mock
async def test_generic_request_error_text(clock: FakeClock) -> None:
    respx.get(f"{BASE}/x").mock(side_effect=httpx.DecodingError("bad body"))
    client = make_client(clock)
    try:
        with pytest.raises(SejmApiError) as excinfo:
            await client.get_json("x")
    finally:
        await client.close()

    assert str(excinfo.value) == "Błędne żądanie do źródła SAOS: bad body"


@pytest.mark.asyncio
@respx.mock
async def test_generic_redirect_refused_text(clock: FakeClock) -> None:
    respx.get(f"{BASE}/x").mock(
        return_value=httpx.Response(302, headers={"Location": "https://elsewhere.example.test/"})
    )
    client = make_client(clock)
    try:
        with pytest.raises(EgressRedirectRefusedError) as excinfo:
            await client.get_json("x")
    finally:
        await client.close()

    assert str(excinfo.value) == "Źródło SAOS przekierowało żądanie poza własny adres, więc go nie wykonano"
