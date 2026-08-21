"""Pure failure-classification policy for Sejm API requests.

The module touches neither the network, the clock, nor shared state, which keeps
the whole retry policy testable offline — including a sweep over every status
code. The network layer (`sejm_client`) only reads the verdict.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import httpx

DEFAULT_BACKOFF_BASE = 1.0
DEFAULT_BACKOFF_CAP = 10.0


@dataclass(frozen=True)
class Verdict:
    """Policy verdict for a single failed attempt.

    Attributes:
        retryable: Whether the attempt may be repeated.
        breaker_failure: Whether the event counts as a circuit breaker failure.
        retry_after: Wait time imposed by the server, in seconds.
        rate_limited: Whether the server reported a rate limit (HTTP 429).
    """

    retryable: bool
    breaker_failure: bool
    retry_after: float | None = None
    rate_limited: bool = False


def _parse_retry_after(raw: str | None) -> float | None:
    """Read a Retry-After header expressed in seconds.

    The HTTP-date form is deliberately skipped — the Sejm API does not return
    it, and an unparsed header degrades safely into the ordinary backoff.
    """
    if raw is None:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


def classify_failure(exc: httpx.HTTPError) -> Verdict:
    """Classify a failed request attempt.

    Args:
        exc: Exception raised by the `httpx` layer.

    Returns:
        A verdict stating whether to retry and whether to book a breaker failure.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        retry_after = _parse_retry_after(exc.response.headers.get("retry-after"))
        if status == 429:
            return Verdict(
                retryable=True,
                breaker_failure=False,
                retry_after=retry_after,
                rate_limited=True,
            )
        if 500 <= status <= 599:
            return Verdict(retryable=True, breaker_failure=True, retry_after=retry_after)
        return Verdict(retryable=False, breaker_failure=False)

    # Both are faults on our side of the wire, not the server's: UnsupportedProtocol
    # is a configuration error, LocalProtocolError means we built a malformed request.
    # A retry would rebuild the same broken request, so it only costs Sejm traffic —
    # and booking it as a breaker failure would open the circuit against a healthy API.
    if isinstance(exc, httpx.UnsupportedProtocol | httpx.LocalProtocolError):
        return Verdict(retryable=False, breaker_failure=False)

    # TimeoutException is an httpx subclass of TransportError, so a single branch
    # covers both F11 and the previous behaviour for timeouts.
    if isinstance(exc, httpx.TransportError):
        return Verdict(retryable=True, breaker_failure=True)

    return Verdict(retryable=False, breaker_failure=False)


def backoff(
    attempt: int,
    *,
    base: float = DEFAULT_BACKOFF_BASE,
    cap: float = DEFAULT_BACKOFF_CAP,
) -> float:
    """Return the exponential delay preceding attempt number `attempt` + 1.

    Args:
        attempt: Number of the attempt just finished, counted from 1.
        base: Delay after the first failed attempt, in seconds.
        cap: Upper bound on the delay, in seconds.

    Returns:
        Number of seconds to wait.
    """
    return min(base * (2 ** (attempt - 1)), cap)
