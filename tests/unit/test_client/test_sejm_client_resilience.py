"""Testy warstwy odporności klienta Sejm API (F10, F11, F19, F20; kryteria 5.1-5.4, 5.7).

Cały zestaw działa offline na respx, a oczekiwanie jest wstrzykiwane przez
podmianę `_delay` — żaden test nie odmierza czasu rzeczywistego (O6).
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
    """Przechwyć żądane opóźnienia zamiast ich odczekiwać."""
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
    """F10, kryterium 5.1: dwie odpowiedzi 503, potem 200 — trzy żądania, dane wracają."""
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
    """F10 + D7, kryterium 5.1: 500 jest ponawiane i kończy się ApiUnavailableError."""
    route = respx.get(ACT_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(ApiUnavailableError):
        await client.get_json(ACT_PATH)

    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_connect_error_is_retried(client: SejmApiClient, slept: list[float]) -> None:
    """F11, kryterium 5.2: błąd transportowy przy pierwszej próbie, sukces przy drugiej."""
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
    """F11, kryterium 5.2: wywołujący nigdy nie widzi surowego wyjątku httpx."""
    respx.get(ACT_URL).mock(side_effect=httpx.ReadError("błąd odczytu"))

    with pytest.raises(ApiUnavailableError):
        await client.get_json(ACT_PATH)


@pytest.mark.asyncio
@respx.mock
async def test_repeated_transport_error_counts_one_failure(
    client: SejmApiClient, breaker: CircuitBreaker, slept: list[float]
) -> None:
    """F11 + F20, kryteria 5.2 i 5.4: trzy próby, dokładnie jedna awaria wyłącznika."""
    respx.get(ACT_URL).mock(side_effect=httpx.ConnectError("brak połączenia"))

    with pytest.raises(ApiUnavailableError):
        await client.get_json(ACT_PATH)

    assert breaker.failure_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_five_failed_operations_open_the_circuit(
    client: SejmApiClient, breaker: CircuitBreaker, slept: list[float]
) -> None:
    """F19 + F20, kryteria 5.3 i 5.4: próg 5 znaczy pięć operacji, nie dwie."""
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
    """F20, kryterium 5.4: przekroczony budżet ucina próby, zamiast zasypiać."""
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
    """F20, kryterium 5.4: suma zaplanowanych oczekiwań mieści się w budżecie."""
    respx.get(ACT_URL).mock(return_value=httpx.Response(503))

    with pytest.raises(ApiUnavailableError):
        await client.get_json(ACT_PATH)

    assert sum(slept) < 45.0


@pytest.mark.asyncio
@respx.mock
async def test_circuit_opened_mid_sequence_aborts_retries(
    client: SejmApiClient, breaker: CircuitBreaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D2, kryterium 5.4: obwód otwarty przez równoległy ruch przerywa naszą sekwencję.

    Podmieniamy `_delay` na taki, który w trakcie oczekiwania symuluje równoległy
    ruch dobijający wyłącznik do progu — to najwierniejsze odwzorowanie wyścigu,
    o którym mówi D2, bez polegania na kolejności zadań.
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
    """F21, kryterium 5.5: dziesięć współbieżnych operacji, trzy docierają do API.

    Wyłącznik z zerowym czasem odzysku wchodzi w HALF_OPEN przy pierwszym
    `try_acquire()`, więc test nie musi manipulować zegarem ani polem prywatnym.
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
        # Punkt zawieszenia jest tu konieczny: bez niego respx zwraca
        # odpowiedź w pełni synchronicznie, więc `asyncio.gather` wykonuje
        # dziesięć zadań kolejno (każde do końca), zanim zacznie kolejne —
        # zero realnej współbieżności i próg HALF_OPEN nigdy nie jest napięty.
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
    """D6, kryterium 5.7: jedno ponowienie po czasie z nagłówka, bez awarii wyłącznika."""
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
    """Kryterium 5.7: 404 nadal podnosi ActNotFoundError, bez ponowień i bez awarii."""
    route = respx.get(ACT_URL).mock(return_value=httpx.Response(404))

    with pytest.raises(ActNotFoundError):
        await client.get_json(ACT_PATH)

    assert route.call_count == 1
    assert breaker.failure_count == 0
    assert slept == []


@pytest.mark.asyncio
@respx.mock
async def test_client_error_stays_a_generic_api_error(client: SejmApiClient, slept: list[float]) -> None:
    """O3 + D7: 4xx inne niż 404 nie awansuje do ApiUnavailableError."""
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
    """Otwarty obwód odrzuca żądanie bez ruchu wychodzącego (O1)."""
    for _ in range(5):
        breaker.release_failure()
    route = respx.get(ACT_URL).mock(return_value=httpx.Response(200, json={"ok": True}))

    with pytest.raises(ApiUnavailableError):
        await client.get_json(ACT_PATH)

    assert route.call_count == 0


@pytest.mark.asyncio
async def test_cancelled_probe_releases_the_half_open_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix (Critical): anulowanie żądania nie może trwale zablokować bezpiecznika.

    `asyncio.CancelledError` nie jest wyjątkiem `httpx`, więc omija klasyfikację
    polityki (`classify_failure` przyjmuje tylko `httpx.HTTPError`). Bez
    dedykowanej obsługi slot HALF_OPEN nigdy nie wraca do zera, a bezpiecznik
    nie może już dojść do `release_failure()` (jedynej drogi z powrotem do OPEN)
    — więc odrzuca wszystko w nieskończoność.
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
    """Fix (Important): awaria 5xx wcześniej w sekwencji nie może zniknąć za 429 na końcu.

    `give_up` patrzył wyłącznie na werdykt ostatniej próby — bez zatrzasku
    ponad wszystkimi próbami sekwencja 500 → 500 → 429 zwalniałaby wyłącznie
    sondę mimo dwóch potwierdzonych awarii serwera, więc wyłącznik nigdy by
    się nie otworzył przeciw API, które degraduje się dokładnie w ten sposób.
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
