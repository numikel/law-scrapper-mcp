"""Pure failure-classification policy for Sejm API requests.

The module touches neither the network nor shared state, which keeps the whole retry
policy testable offline — including a sweep over every status code. The network layer
(`sejm_client`) only reads the verdict.

It was clock-free too until the date form of `Retry-After` had to be honoured; that one
reading is confined to `_now()`. This is a documented deviation from the Klaster 8 plan,
which froze this module precisely because it was pure — see the cluster review.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

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


def _now() -> datetime:
    """Sole clock reading of this module, extracted so tests can pin it.

    Follows the `_wait`/`_delay` pattern this codebase already uses for its other
    unavoidable side channels. The module was written to be clock-free and says so at
    the top; reading the date form of `Retry-After` is what made a clock unavoidable,
    and confining it to one function keeps that deviation visible and testable rather
    than sprinkled through the parser.
    """
    return datetime.now(UTC)


def _http_date_delta(candidate: str) -> float | None:
    """Turn an HTTP-date into seconds from now, or `None` if it is not one."""
    try:
        when = parsedate_to_datetime(candidate)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        # RFC 9110 dates are GMT; a naive result means the zone was simply absent.
        when = when.replace(tzinfo=UTC)
    delta = (when - _now()).total_seconds()
    # An expired date is a stale instruction, not a server saying "retry now". Reading it
    # as zero would mean retrying with no backoff at all, which is the opposite of what a
    # `Retry-After` is for; ordinary backoff is the polite answer.
    return delta if delta > 0 else None


def _single_retry_after(text: str) -> float | None:
    """Read one Retry-After value in either of the two forms RFC 9110 defines."""
    candidate = text.strip()
    try:
        seconds = float(candidate)
    except ValueError:
        return _http_date_delta(candidate)
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


def _parse_retry_after(raw: str | None) -> float | None:
    """Read a Retry-After header in both forms RFC 9110 allows.

    The HTTP-date form used to be skipped, on the stated grounds that the Sejm API does
    not send it. That premise stopped carrying enough weight once the header began
    driving a client-wide pause instead of one request's backoff: the API answers from
    behind a WAF, a WAF is free to reply in the date form, and an unparsed value means
    the pause never engages — every other caller keeps its full pace at a server that
    just asked for quiet. The failure is silent, which is the worst part of it.

    Duplicate headers, which `httpx` joins into `"60, 120"`, are resolved to the longest
    wait and only after a whole-string parse has failed: an HTTP-date contains commas
    itself, so splitting first would shred a valid header.
    """
    if raw is None:
        return None
    candidate = raw.strip()
    single = _single_retry_after(candidate)
    if single is not None or "," not in candidate:
        return single
    waits = [w for w in (_single_retry_after(part) for part in candidate.split(",")) if w is not None]
    return max(waits) if waits else None


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
