"""Tests for the pure failure-classification policy (finding F14, criterion 5.6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from law_scrapper_mcp.client import failure_policy
from law_scrapper_mcp.client.failure_policy import Verdict, backoff, classify_failure

REQUEST = httpx.Request("GET", "https://api.sejm.gov.pl/eli/acts/DU/2024/1")


def _status_error(status: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    response = httpx.Response(status, request=REQUEST, headers=headers or {})
    return httpx.HTTPStatusError(f"HTTP {status}", request=REQUEST, response=response)


@pytest.mark.parametrize("status", [500, 501, 502, 503, 504, 505, 599])
def test_5xx_is_retryable_and_counts_as_breaker_failure(status: int) -> None:
    """F19: the whole 5xx range opens the circuit, not just 502/503."""
    verdict = classify_failure(_status_error(status))
    assert verdict.retryable is True
    assert verdict.breaker_failure is True
    assert verdict.rate_limited is False


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 410, 418, 422])
def test_4xx_other_than_429_is_terminal(status: int) -> None:
    """A request error, not a server one — retrying would load Sejm for nothing."""
    verdict = classify_failure(_status_error(status))
    assert verdict.retryable is False
    assert verdict.breaker_failure is False


def test_429_is_retryable_but_never_a_breaker_failure() -> None:
    """D6: 429 means "we are overdoing it", not "Sejm is down"."""
    verdict = classify_failure(_status_error(429))
    assert verdict.retryable is True
    assert verdict.breaker_failure is False
    assert verdict.rate_limited is True


def test_429_honours_numeric_retry_after() -> None:
    verdict = classify_failure(_status_error(429, {"Retry-After": "2"}))
    assert verdict.retry_after == pytest.approx(2.0)


@pytest.mark.parametrize("raw", ["", "later", "-5", "inf", "nan", "1e400"])
def test_unparsable_retry_after_falls_back_to_backoff(raw: str) -> None:
    """A junk header must not upset the classification."""
    verdict = classify_failure(_status_error(429, {"Retry-After": raw}))
    assert verdict.retryable is True
    assert verdict.retry_after is None


PINNED_NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def pinned_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the module's one clock reading.

    Comparing against a live `datetime.now()` would need a tolerance window, and this
    suite's rule is that no assertion depends on real elapsed time. `_now` exists to be
    pinned, exactly like `_wait` and `_delay` elsewhere in the client.
    """
    monkeypatch.setattr(failure_policy, "_now", lambda: PINNED_NOW)


def test_429_honours_an_http_date_retry_after(pinned_clock: None) -> None:
    """The date form drives the pause too, now that the pause is client-wide.

    This reverses an earlier decision to skip the date form on the grounds that the
    Sejm API does not send it. The API answers from behind a WAF, and an unparsed
    header does not fail loudly — it just means the global pause never engages while
    every other caller keeps its pace at a server that asked for quiet.
    """
    when = format_datetime(PINNED_NOW + timedelta(seconds=120), usegmt=True)

    verdict = classify_failure(_status_error(429, {"Retry-After": when}))

    assert verdict.retry_after == pytest.approx(120.0)


def test_an_expired_http_date_falls_back_to_backoff(pinned_clock: None) -> None:
    """A date already past is a stale instruction, not "retry immediately".

    Reading it as zero would retry with no backoff at all — the opposite of what a
    `Retry-After` asks for.
    """
    when = format_datetime(PINNED_NOW - timedelta(hours=1), usegmt=True)

    verdict = classify_failure(_status_error(429, {"Retry-After": when}))

    assert verdict.retry_after is None


def test_duplicate_retry_after_headers_resolve_to_the_longest_wait(pinned_clock: None) -> None:
    """`httpx` joins repeated headers into one comma-separated value.

    Parsed as a single value it is junk, which used to silence the pause entirely.
    The longest of the two is the polite reading.
    """
    verdict = classify_failure(_status_error(429, {"Retry-After": "60, 120"}))

    assert verdict.retry_after == pytest.approx(120.0)


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("brak połączenia", request=REQUEST),
        httpx.ReadError("błąd odczytu", request=REQUEST),
        httpx.WriteError("błąd zapisu", request=REQUEST),
        httpx.ConnectTimeout("przekroczono czas łączenia", request=REQUEST),
        httpx.ReadTimeout("przekroczono czas odczytu", request=REQUEST),
        httpx.PoolTimeout("przekroczono czas puli", request=REQUEST),
        httpx.WriteTimeout("przekroczono czas zapisu", request=REQUEST),
        httpx.RemoteProtocolError("błąd protokołu", request=REQUEST),
        httpx.ProxyError("błąd proxy", request=REQUEST),
        httpx.CloseError("błąd zamknięcia", request=REQUEST),
    ],
)
def test_transport_errors_are_retryable_failures(exc: httpx.TransportError) -> None:
    """F11: one branch covers timeouts and the remaining transport errors."""
    verdict = classify_failure(exc)
    assert verdict.retryable is True
    assert verdict.breaker_failure is True


@pytest.mark.parametrize(
    "exc",
    [
        httpx.DecodingError("błąd dekodowania", request=REQUEST),
        httpx.TooManyRedirects("za dużo przekierowań", request=REQUEST),
        httpx.UnsupportedProtocol("nieobsługiwany protokół", request=REQUEST),
        httpx.LocalProtocolError("źle zbudowane żądanie"),
    ],
)
def test_non_transport_request_errors_are_terminal(exc: httpx.RequestError) -> None:
    """Contract or configuration errors — a retry can never help.

    `LocalProtocolError` sits under `TransportError` in the httpx hierarchy but is
    a fault on our side of the wire: retrying rebuilds the same malformed request,
    costing Sejm traffic (O1) and risking a circuit opened against a healthy API.
    """
    verdict = classify_failure(exc)
    assert verdict.retryable is False
    assert verdict.breaker_failure is False


@pytest.mark.parametrize("status", list(range(100, 600)))
def test_every_status_code_has_a_defined_verdict(status: int) -> None:
    """5.6: sweep 100-599 — no status code may fall out of the classification."""
    verdict = classify_failure(_status_error(status))
    assert isinstance(verdict, Verdict)
    if 500 <= status <= 599:
        assert verdict.retryable and verdict.breaker_failure
    elif status == 429:
        assert verdict.retryable and not verdict.breaker_failure
    else:
        assert not verdict.retryable and not verdict.breaker_failure


def test_verdict_is_immutable() -> None:
    """The verdict is a value, not state — nobody can swap it along the way."""
    verdict = classify_failure(_status_error(503))
    with pytest.raises(AttributeError):
        verdict.retryable = False  # type: ignore[misc]


@pytest.mark.parametrize(
    ("attempt", "expected"),
    [(1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (5, 10.0), (10, 10.0)],
)
def test_backoff_is_exponential_and_capped(attempt: int, expected: float) -> None:
    """Preserves the shape of the removed wait_exponential(min=1, max=10)."""
    assert backoff(attempt) == pytest.approx(expected)
