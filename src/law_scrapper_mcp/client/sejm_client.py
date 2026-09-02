"""Async HTTP client for Sejm API."""

from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from time import monotonic
from typing import Any

import httpx

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.circuit_breaker import CircuitBreaker
from law_scrapper_mcp.client.exceptions import (
    ActNotFoundError,
    ApiUnavailableError,
    SejmApiError,
)
from law_scrapper_mcp.client.failure_policy import backoff, classify_failure
from law_scrapper_mcp.client.rate_limiter import EgressPaceDeadlineError, RateLimiter

logger = logging.getLogger(__name__)

# Deliberately carries no version: a hardcoded one drifts, and a client built
# without settings has no honest version to claim. Production passes
# `Settings.user_agent`, which derives both name and version from configuration.
DEFAULT_USER_AGENT = "law-scrapper-mcp"


class RequestClass(StrEnum):
    """Traffic class deciding which concurrency budget a request draws on.

    Split so that a run of act downloads can never take the last slot a search would
    have used (finding F55). The rate limiter stays shared: pace is a property of all
    outbound traffic, not of one class.
    """

    LIGHT = "light"
    HEAVY = "heavy"


async def _delay(seconds: float) -> None:
    """Sole waiting point of the retry loop.

    Extracted so that tests can substitute the delay instead of sitting it out.
    """
    await asyncio.sleep(seconds)


class SejmApiClient:
    """Async HTTP client for Sejm API with retry, caching and circuit breaker."""

    BASE_URL = "https://api.sejm.gov.pl/eli"

    def __init__(
        self,
        cache: TTLCache,
        timeout: float = 30.0,
        max_concurrent: int = 8,
        circuit_breaker: CircuitBreaker | None = None,
        max_attempts: int = 3,
        retry_budget: float = 45.0,
        user_agent: str = DEFAULT_USER_AGENT,
        max_concurrent_content: int = 2,
        rate_per_second: float = 5.0,
        rate_burst: int = 10,
        max_server_pause: float = 60.0,
        rate_limiter: RateLimiter | None = None,
    ):
        self._client: httpx.AsyncClient | None = None
        self._cache = cache
        self._semaphores = {
            RequestClass.LIGHT: asyncio.Semaphore(max_concurrent),
            RequestClass.HEAVY: asyncio.Semaphore(max_concurrent_content),
        }
        self._timeout = timeout
        self._circuit_breaker = circuit_breaker or CircuitBreaker()
        # Prebuilt limiter accepted on the same terms as the breaker above: production
        # passes rate, burst and the pause cap, tests pass a limiter with an injected
        # clock. A limiter injected here MUST share the clock this class reads for
        # deadlines (module-level `monotonic`), or the pacing bound silently fails open
        # — the limiter compares a deadline it cannot interpret and simply never refuses.
        self._rate_limiter = rate_limiter or RateLimiter(
            rate=rate_per_second, burst=rate_burst, max_pause=max_server_pause
        )
        self._max_attempts = max_attempts
        self._retry_budget = retry_budget
        self._user_agent = user_agent

    async def start(self) -> None:
        """Initialize the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=self._timeout, write=10.0, pool=10.0),
                headers={
                    "User-Agent": self._user_agent,
                    "Accept": "application/json",
                },
                follow_redirects=True,
            )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _send(
        self,
        method: str,
        path: str,
        *,
        request_class: RequestClass = RequestClass.LIGHT,
        deadline: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a single HTTP request.

        Lowest layer: builds the URL, waits its turn and guards the class semaphore. It
        neither retries nor translates errors, and every error it raises about the wire
        is an `httpx` one — the single exception is its own pacing refusal below.

        The token comes before the slot, and the order is load-bearing: a request
        waiting for a token must not hold a concurrency slot, or rate limiting quietly
        becomes a second concurrency limit.

        `deadline` is the operation's own time budget, forwarded to the limiter so that
        pacing cannot outlive it. Without it the wait for a token — up to the limiter's
        `max_pause` after a server-requested pause — sits below the layer that owns the
        budget and is invisible to it, so `api_retry_budget` stops bounding anything on
        the success path.

        Raises:
            httpx.HTTPError: Any transport or status error.
            EgressPaceDeadlineError: Pacing alone would exhaust `deadline`. Deliberately
                not an `httpx` error: it is our own policy speaking, not the wire, and
                the retry loop must not reclassify it as an upstream failure.
        """
        if self._client is None:
            await self.start()

        assert self._client is not None  # ensured by start()

        url = f"{self.BASE_URL}/{path.lstrip('/')}"

        await self._rate_limiter.acquire(deadline=deadline)

        async with self._semaphores[request_class]:
            response = await self._client.request(method, url, **kwargs)
            response.raise_for_status()
            return response

    async def _execute_with_resilience(
        self,
        method: str,
        path: str,
        *,
        request_class: RequestClass = RequestClass.LIGHT,
        **kwargs: Any,
    ) -> httpx.Response:
        """Run a request with retries, a time budget and breaker accounting.

        The middle layer sees raw `httpx` exceptions only, so no `except` block
        can change the type before the policy gets to judge it.

        One user operation records at most one breaker failure, and the breaker
        state is checked before every attempt — a circuit opened by concurrent
        traffic aborts the sequence instead of hammering the API further.

        Raises:
            ApiUnavailableError: When the breaker refuses admission.
            EgressPaceDeadlineError: When pacing alone would exhaust the budget. Passes
                through untouched: `classify_failure` never sees it, so this loop cannot
                mistake our own policy for an upstream failure.
            httpx.HTTPError: The last error once attempts or budget run out.
        """
        deadline = monotonic() + self._retry_budget
        throttled = False
        breaker_failure_seen = False

        for attempt in range(1, self._max_attempts + 1):
            if not self._circuit_breaker.try_acquire():
                # A failure already confirmed earlier in this sequence must not
                # vanish just because admission was refused before we could book
                # it. In HALF_OPEN that loss would let a still-broken API be
                # declared recovered by the probes that happened to succeed.
                if breaker_failure_seen:
                    self._circuit_breaker.release_failure()
                raise ApiUnavailableError(
                    "API Sejmu tymczasowo niedostępne (bezpiecznik otwarty)",
                    status_code=503,
                )

            try:
                response = await self._send(method, path, request_class=request_class, deadline=deadline, **kwargs)
            except httpx.HTTPError as exc:
                verdict = classify_failure(exc)
                if verdict.retry_after is not None:
                    # The pause belongs to the whole client, not to this one request.
                    # Nine concurrent callers each honouring a private copy of the same
                    # header would come back as a herd, aimed at a server that just
                    # asked for quiet. Applied before the give-up check on purpose: the
                    # signal outlives the request that happened to receive it.
                    #
                    # Handed over unclamped on purpose: the limiter bounds the pause to
                    # its own `max_pause`, and the clamp must not happen earlier — in
                    # `classify_failure`/`_parse_retry_after` or here — because
                    # `verdict.retry_after` also drives `delay` and `give_up` below, and
                    # clamping it would silently turn "give up now" into "retry after
                    # the clamp" for this one request. Only the client-wide pause is
                    # bounded; this request's own retry policy sees the real header.
                    self._rate_limiter.pause_for(verdict.retry_after)
                if verdict.breaker_failure:
                    breaker_failure_seen = True
                delay = verdict.retry_after if verdict.retry_after is not None else backoff(attempt)

                give_up = (
                    not verdict.retryable
                    or attempt == self._max_attempts
                    or (verdict.rate_limited and throttled)
                    or monotonic() + delay >= deadline
                )

                if give_up:
                    if breaker_failure_seen:
                        self._circuit_breaker.release_failure()
                    else:
                        self._circuit_breaker.release_probe()
                    raise

                self._circuit_breaker.release_probe()
                if verdict.rate_limited:
                    throttled = True
                await _delay(delay)
            except BaseException:
                # Same accounting as the admission-refused branch above: a failure
                # already booked in this sequence must not vanish because the sequence
                # ended some other way — a cancellation, or pacing refusing a wait it
                # cannot afford. Releasing it as a clean probe would let a still-broken
                # API be declared recovered in HALF_OPEN.
                if breaker_failure_seen:
                    self._circuit_breaker.release_failure()
                else:
                    self._circuit_breaker.release_probe()
                raise
            else:
                self._circuit_breaker.release_success()
                return response

        # Unreachable: the loop always exits through a return or a raise.
        raise ApiUnavailableError("API Sejmu nie odpowiedziało w ramach dozwolonych prób", status_code=503)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        request_class: RequestClass = RequestClass.LIGHT,
        **kwargs: Any,
    ) -> httpx.Response:
        """Run a request and translate `httpx` errors into domain exceptions.

        Topmost layer. Translation happens outside the retry loop, so the policy
        always judges the original exception type.

        Args:
            method: HTTP method.
            path: Path relative to BASE_URL.
            **kwargs: Additional httpx request parameters.

        Returns:
            HTTP response.

        Raises:
            ActNotFoundError: When the resource does not exist (404).
            ApiUnavailableError: When the API returned 5xx, transport failed,
                or the circuit breaker is open.
            SejmApiError: For the remaining request-side errors.
        """
        try:
            return await self._execute_with_resilience(method, path, request_class=request_class, **kwargs)
        except EgressPaceDeadlineError as exc:
            # Reported as unavailability, which is what it is from the caller's side —
            # but the message says who is holding the traffic back, so an agent is not
            # told "Sejm is down" when the truth is "we are keeping the pace we promised".
            # `max(1, ...)` because a sub-second wait floored to "~0 s" reads as "no wait
            # at all" and invites an immediate retry. The two causes get different
            # sentences: only a server-requested pause is a halt — the other is this
            # client keeping the pace it promised, and saying "halted" there would
            # describe a condition that does not exist.
            seconds = max(1, round(exc.wait))
            cause = (
                f"Ruch do API Sejmu jest wstrzymany na prośbę serwera (~{seconds} s)"
                if exc.paused
                else f"Żądanie musiałoby czekać w kolejce na przepustowość (~{seconds} s)"
            )
            raise ApiUnavailableError(
                f"{cause} — nie zmieściłoby się w budżecie czasu operacji. Spróbuj ponownie za chwilę.",
                status_code=503,
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            url = str(exc.request.url)
            if status == 404:
                raise ActNotFoundError(path) from exc
            if 500 <= status <= 599:
                raise ApiUnavailableError(
                    f"API Sejmu chwilowo niedostępne (HTTP {status})",
                    status_code=status,
                    url=url,
                ) from exc
            raise SejmApiError(
                f"HTTP {status}: {exc.response.text}",
                status_code=status,
                url=url,
            ) from exc
        except httpx.TransportError as exc:
            raise ApiUnavailableError(f"Błąd połączenia z API Sejmu: {exc}") from exc
        except httpx.HTTPError as exc:
            raise SejmApiError(f"Błędne żądanie do API Sejmu: {exc}") from exc

    async def get_json(self, path: str, params: dict[str, Any] | None = None, cache_ttl: int | None = None) -> Any:
        """Get JSON response from API with optional caching.

        Args:
            path: URL path
            params: Query parameters
            cache_ttl: Cache TTL in seconds (None = no cache)

        Returns:
            Parsed JSON response
        """
        # Build cache key
        cache_key = None
        if cache_ttl is not None:
            cache_key = f"json:{path}:{params or {}}"
            cached = await self._cache.get(cache_key)
            if cached is not None:
                return cached

        response = await self._request("GET", path, params=params)
        data = response.json()

        if cache_key is not None and cache_ttl is not None:
            await self._cache.set(cache_key, data, cache_ttl)

        return data

    async def get_text(self, path: str) -> str:
        """Get text response from API.

        Args:
            path: URL path

        Returns:
            Response text
        """
        response = await self._request(
            "GET",
            path,
            request_class=RequestClass.HEAVY,
            headers={"Accept": "text/html, text/plain, */*"},
        )
        return response.text

    async def get_bytes(self, path: str) -> bytes:
        """Get binary response from API.

        Args:
            path: URL path

        Returns:
            Response bytes
        """
        response = await self._request(
            "GET",
            path,
            request_class=RequestClass.HEAVY,
            headers={"Accept": "application/pdf, application/octet-stream, */*"},
        )
        return response.content

    async def get_act(self, publisher: str, year: int, pos: int) -> dict[str, Any]:
        """Get act details.

        Args:
            publisher: Publisher code
            year: Year
            pos: Position number

        Returns:
            Act details as dict
        """
        path = f"acts/{publisher}/{year}/{pos}"
        return await self.get_json(path)

    async def search_acts(self, params: dict[str, Any]) -> dict[str, Any]:
        """Search for acts.

        Args:
            params: Search parameters

        Returns:
            Search results as dict
        """
        return await self.get_json("acts/search", params=params)

    async def get_act_structure(self, publisher: str, year: int, pos: int) -> list[dict[str, Any]]:
        """Get act table of contents structure.

        Args:
            publisher: Publisher code
            year: Year
            pos: Position number

        Returns:
            Structure as list of dicts
        """
        path = f"acts/{publisher}/{year}/{pos}/struct"
        return await self.get_json(path)

    async def get_act_references(self, publisher: str, year: int, pos: int) -> dict[str, Any]:
        """Get act references/relationships.

        Args:
            publisher: Publisher code
            year: Year
            pos: Position number

        Returns:
            References as dict
        """
        path = f"acts/{publisher}/{year}/{pos}/references"
        return await self.get_json(path)

    async def get_act_html(self, publisher: str, year: int, pos: int) -> str:
        """Get act HTML content.

        Args:
            publisher: Publisher code
            year: Year
            pos: Position number

        Returns:
            HTML content
        """
        path = f"acts/{publisher}/{year}/{pos}/text.html"
        return await self.get_text(path)

    async def get_act_pdf_url(self, publisher: str, year: int, pos: int) -> str:
        """Get act PDF URL.

        Args:
            publisher: Publisher code
            year: Year
            pos: Position number

        Returns:
            PDF URL
        """
        return f"{self.BASE_URL}/acts/{publisher}/{year}/{pos}/text.pdf"

    async def get_metadata(self, endpoint: str) -> list[dict[str, Any]] | dict[str, Any]:
        """Get metadata from endpoint.

        Args:
            endpoint: Metadata endpoint (e.g., "keywords", "publishers")

        Returns:
            Metadata as list or dict
        """
        return await self.get_json(endpoint)
