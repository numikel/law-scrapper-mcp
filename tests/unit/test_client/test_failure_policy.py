"""Testy czystej polityki klasyfikacji błędów (ustalenie F14, kryterium 5.6)."""

from __future__ import annotations

import httpx
import pytest

from law_scrapper_mcp.client.failure_policy import Verdict, backoff, classify_failure

REQUEST = httpx.Request("GET", "https://api.sejm.gov.pl/eli/acts/DU/2024/1")


def _status_error(status: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    response = httpx.Response(status, request=REQUEST, headers=headers or {})
    return httpx.HTTPStatusError(f"HTTP {status}", request=REQUEST, response=response)


@pytest.mark.parametrize("status", [500, 501, 502, 503, 504, 505, 599])
def test_5xx_is_retryable_and_counts_as_breaker_failure(status: int) -> None:
    """F19: całe 5xx otwiera obwód, nie tylko 502/503."""
    verdict = classify_failure(_status_error(status))
    assert verdict.retryable is True
    assert verdict.breaker_failure is True
    assert verdict.rate_limited is False


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 410, 418, 422])
def test_4xx_other_than_429_is_terminal(status: int) -> None:
    """Błąd zapytania, nie serwera — ponawianie obciążałoby Sejm bez szansy na inny wynik."""
    verdict = classify_failure(_status_error(status))
    assert verdict.retryable is False
    assert verdict.breaker_failure is False


def test_429_is_retryable_but_never_a_breaker_failure() -> None:
    """D6: 429 znaczy 'my przesadzamy', nie 'Sejm padł'."""
    verdict = classify_failure(_status_error(429))
    assert verdict.retryable is True
    assert verdict.breaker_failure is False
    assert verdict.rate_limited is True


def test_429_honours_numeric_retry_after() -> None:
    verdict = classify_failure(_status_error(429, {"Retry-After": "2"}))
    assert verdict.retry_after == pytest.approx(2.0)


@pytest.mark.parametrize("raw", ["", "later", "-5", "Wed, 21 Oct 2026 07:28:00 GMT"])
def test_unparsable_retry_after_falls_back_to_backoff(raw: str) -> None:
    """Nagłówek w formacie daty lub śmieciowy nie może wywrócić klasyfikacji."""
    verdict = classify_failure(_status_error(429, {"Retry-After": raw}))
    assert verdict.retryable is True
    assert verdict.retry_after is None


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("brak połączenia", request=REQUEST),
        httpx.ReadError("błąd odczytu", request=REQUEST),
        httpx.WriteError("błąd zapisu", request=REQUEST),
        httpx.ConnectTimeout("przekroczono czas łączenia", request=REQUEST),
        httpx.ReadTimeout("przekroczono czas odczytu", request=REQUEST),
        httpx.PoolTimeout("przekroczono czas puli", request=REQUEST),
        httpx.RemoteProtocolError("błąd protokołu", request=REQUEST),
        httpx.ProxyError("błąd proxy", request=REQUEST),
    ],
)
def test_transport_errors_are_retryable_failures(exc: httpx.TransportError) -> None:
    """F11: jeden blok obejmuje timeouty i pozostałe błędy transportowe."""
    verdict = classify_failure(exc)
    assert verdict.retryable is True
    assert verdict.breaker_failure is True


@pytest.mark.parametrize(
    "exc",
    [
        httpx.DecodingError("błąd dekodowania", request=REQUEST),
        httpx.TooManyRedirects("za dużo przekierowań", request=REQUEST),
        httpx.UnsupportedProtocol("nieobsługiwany protokół", request=REQUEST),
    ],
)
def test_non_transport_request_errors_are_terminal(exc: httpx.RequestError) -> None:
    """Błędy kontraktu lub konfiguracji — ponowienie nigdy nie pomoże."""
    verdict = classify_failure(exc)
    assert verdict.retryable is False
    assert verdict.breaker_failure is False


@pytest.mark.parametrize("status", list(range(100, 600)))
def test_every_status_code_has_a_defined_verdict(status: int) -> None:
    """5.6: przemiatanie 100–599 — żaden kod nie może wypaść z klasyfikacji."""
    verdict = classify_failure(_status_error(status))
    assert isinstance(verdict, Verdict)
    if 500 <= status <= 599:
        assert verdict.retryable and verdict.breaker_failure
    elif status == 429:
        assert verdict.retryable and not verdict.breaker_failure
    else:
        assert not verdict.retryable and not verdict.breaker_failure


def test_verdict_is_immutable() -> None:
    """Werdykt jest wartością, nie stanem — nikt go po drodze nie podmieni."""
    verdict = classify_failure(_status_error(503))
    with pytest.raises(AttributeError):
        verdict.retryable = False  # type: ignore[misc]


@pytest.mark.parametrize(
    ("attempt", "expected"),
    [(1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (5, 10.0), (10, 10.0)],
)
def test_backoff_is_exponential_and_capped(attempt: int, expected: float) -> None:
    """Zachowuje charakterystykę usuwanego wait_exponential(min=1, max=10)."""
    assert backoff(attempt) == pytest.approx(expected)
