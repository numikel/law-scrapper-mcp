"""Host-agnostic egress client: one upstream host, one set of controls.

Everything that decides how politely and how resiliently this server talks to a host lives
here: the token bucket, the two concurrency lanes, the retry loop with its time budget, the
circuit breaker accounting and the streamed byte budget. `SejmApiClient` is a thin subclass that
adds the Sejm's own endpoints; a source module gets its own instance for its own host, so no
limiter, breaker or connection pool is ever shared between two hosts.
"""

from __future__ import annotations

import asyncio
import logging
import re
import ssl
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

import httpx

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.circuit_breaker import CircuitBreaker, CircuitState
from law_scrapper_mcp.client.exceptions import (
    SEJM_SOURCE,
    ActNotFoundError,
    ApiUnavailableError,
    EgressConfigError,
    EgressRedirectRefusedError,
    ResponseTooLargeError,
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


@dataclass(frozen=True, slots=True)
class HostMessages:
    """The client-side texts an agent can end up reading for one host.

    Format strings, so that the Sejm keeps the exact wording it has always had while other
    hosts get a neutral one. Named fields: `{seconds}` in the two pacing texts, `{status}`,
    `{error}`, `{method}`, `{path}` and `{detail}` where the text says so.
    """

    breaker_open: str
    pacing_paused: str
    pacing_queued: str
    upstream_status: str
    transport_failure: str
    request_error: str
    redirect_refused: str

    @classmethod
    def generic(cls, name: str) -> HostMessages:
        """Neutral Polish wording naming the source, for a host that has no preset.

        The pacing texts end at the time budget on purpose: the retry advice is the tool
        layer's category guidance, so it is not written twice.
        """
        return cls(
            breaker_open=f"Źródło {name} jest tymczasowo niedostępne (bezpiecznik otwarty)",
            pacing_paused=(
                f"Ruch do źródła {name} jest wstrzymany na prośbę serwera (~{{seconds}} s) — "
                "nie zmieściłoby się w budżecie czasu operacji"
            ),
            pacing_queued=(
                f"Żądanie do źródła {name} musiałoby czekać w kolejce na przepustowość (~{{seconds}} s) — "
                "nie zmieściłoby się w budżecie czasu operacji"
            ),
            upstream_status=f"Źródło {name} jest chwilowo niedostępne (HTTP {{status}})",
            transport_failure=f"Błąd połączenia ze źródłem {name} ({{error}}) podczas żądania {{method}} {{path}}",
            request_error=f"Błędne żądanie do źródła {name}: {{detail}}",
            redirect_refused=f"Źródło {name} przekierowało żądanie poza własny adres, więc go nie wykonano",
        )


# Reproduces, word for word, the texts this client used before it was made host-agnostic.
# `tests/unit/test_client/test_sejm_message_texts.py` pins every one of them as a literal.
SEJM_MESSAGES = HostMessages(
    breaker_open="API Sejmu tymczasowo niedostępne (bezpiecznik otwarty)",
    pacing_paused=(
        "Ruch do API Sejmu jest wstrzymany na prośbę serwera (~{seconds} s) — "
        "nie zmieściłoby się w budżecie czasu operacji. Spróbuj ponownie za chwilę."
    ),
    pacing_queued=(
        "Żądanie musiałoby czekać w kolejce na przepustowość (~{seconds} s) — "
        "nie zmieściłoby się w budżecie czasu operacji. Spróbuj ponownie za chwilę."
    ),
    upstream_status="API Sejmu chwilowo niedostępne (HTTP {status})",
    transport_failure="Błąd połączenia z API Sejmu ({error}) podczas żądania {method} {path}",
    request_error="Błędne żądanie do API Sejmu: {detail}",
    redirect_refused="API Sejmu przekierowało żądanie poza własny adres, więc go nie wykonano",
)


# RFC 9110 `token`: the characters a product name or version may be made of in `User-Agent`.
_TOKEN = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+")
# Short label shown in logs, in agent-facing errors and on the unauthenticated `/health` route.
_HOST_NAME = re.compile(r"[A-Za-z0-9 ._-]{1,64}")
# The most redirect hops a host built through the hub may follow.
MAX_MODULE_REDIRECTS = 3


@dataclass(frozen=True, slots=True)
class CallerIdentity:
    """Who generates the traffic: the module a request is made on behalf of.

    Both parts end up in the `User-Agent` header as an RFC 9110 product token
    (`module/version`), so they must be tokens: no spaces, parentheses, slashes or control
    characters.

    Attributes:
        module: Name of the source module, for example `law-scrapper-saos`.
        version: Version of that module.
    """

    module: str
    version: str

    def __post_init__(self) -> None:
        for label, value in (("module", self.module), ("version", self.version)):
            if not _TOKEN.fullmatch(value):
                raise EgressConfigError(
                    f"Nieprawidłowa tożsamość wywołującego: pole {label} musi być znacznikiem RFC 9110 "
                    "(bez spacji, nawiasów, ukośników i znaków sterujących)."
                )

    @property
    def product(self) -> str:
        """The `module/version` product token appended to the core identity."""
        return f"{self.module}/{self.version}"


@dataclass(frozen=True, slots=True)
class EgressHost:
    """What the traffic is addressed to.

    Attributes:
        name: Short label used in logs and errors; the Sejm uses its host name.
        base_url: Scheme, host, optional port and path prefix every request path is appended to.
        headers: Extra default request headers.
        verify: TLS verification setting handed to `httpx`.
        rate_per_second: Request rate for this host; `None` takes the default of the owner.
        rate_burst: Bucket capacity for this host; `None` takes the default of the owner.
        messages: Client-side texts; `None` means a neutral wording built from `name`.
        pin_origin: Refuse a redirect hop to another scheme, host or port.
        max_redirects: Longest redirect chain `httpx` follows.
    """

    name: str
    base_url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    verify: ssl.SSLContext | bool = True
    rate_per_second: float | None = None
    rate_burst: int | None = None
    messages: HostMessages | None = None
    pin_origin: bool = True
    max_redirects: int = 3

    def __post_init__(self) -> None:
        if not _HOST_NAME.fullmatch(self.name) or self.name != self.name.strip():
            raise EgressConfigError(
                "Nazwa źródła musi mieć 1-64 znaków: litery, cyfry, spacja, kropka, myślnik lub podkreślenie."
            )
        object.__setattr__(self, "base_url", _validated_base_url(self.base_url))
        if any(key.lower() in ("user-agent", "host") for key in self.headers):
            raise EgressConfigError(
                f"Nagłówki User-Agent i Host ustawia serwer, a źródło {self.name} próbuje je nadpisać."
            )
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
        if self.max_redirects < 0:
            raise EgressConfigError(f"Źródło {self.name}: limit przekierowań nie może być ujemny.")
        if self.rate_per_second is not None and self.rate_per_second <= 0:
            raise EgressConfigError(f"Źródło {self.name}: tempo żądań musi być większe od zera.")
        if self.rate_burst is not None and self.rate_burst < 1:
            raise EgressConfigError(f"Źródło {self.name}: pojemność kubełka musi wynosić co najmniej 1.")


def _validated_base_url(base_url: str) -> str:
    """Return `base_url` without a trailing slash, or raise `EgressConfigError` if it is unusable."""
    try:
        parts = urlsplit(base_url)
        _ = parts.port  # raises ValueError for a malformed port
    except ValueError as exc:
        raise EgressConfigError("Adres źródła jest nieprawidłowy.") from exc
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or "?" in base_url
        or "#" in base_url
    ):
        raise EgressConfigError(
            "Adres źródła musi mieć postać http(s)://host[:port][/ścieżka], bez danych logowania, zapytania i fragmentu."
        )
    return base_url.rstrip("/")


def _origin_of(url: httpx.URL) -> str:
    """`scheme://host[:port]` of a URL, the unit a host is identified and its cache keyed by."""
    port = f":{url.port}" if url.port is not None else ""
    return f"{url.scheme}://{url.host}{port}"


async def _delay(seconds: float) -> None:
    """Sole waiting point of the retry loop.

    Extracted so that tests can substitute the delay instead of sitting it out.
    """
    await asyncio.sleep(seconds)


class EgressClient:
    """Async HTTP client for one upstream host, with retry, caching and a circuit breaker."""

    def __init__(
        self,
        host: EgressHost,
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
        clock: Callable[[], float] = monotonic,
    ):
        self._host = host
        self._base_url = host.base_url
        self._origin = _origin_of(httpx.URL(host.base_url))
        # Log label for the limiter and breaker built here. The Sejm stays unlabelled so that
        # its log lines read exactly as they always have; an unlabelled line is the Sejm's.
        log_name = "" if host.name == SEJM_SOURCE else host.name
        self._client: httpx.AsyncClient | None = None
        self._cache = cache
        self._semaphores = {
            RequestClass.LIGHT: asyncio.Semaphore(max_concurrent),
            RequestClass.HEAVY: asyncio.Semaphore(max_concurrent_content),
        }
        self._timeout = timeout
        self._circuit_breaker = circuit_breaker or CircuitBreaker(name=log_name)
        # Prebuilt limiter accepted on the same terms as the breaker above: production
        # passes rate, burst and the pause cap, tests pass a limiter with an injected
        # clock. The deadline this class computes and the waits the limiter measures
        # must be on one scale, or the pacing bound silently fails open — the limiter
        # compares a deadline it cannot interpret and simply never refuses. So the two
        # clocks are never configured independently: an injected limiter lends the
        # client its clock, and a limiter built here receives the client's.
        if rate_limiter is None:
            rate_limiter = RateLimiter(
                rate=rate_per_second, burst=rate_burst, max_pause=max_server_pause, clock=clock, name=log_name
            )
        self._rate_limiter = rate_limiter
        self._clock = rate_limiter.clock
        self._max_attempts = max_attempts
        self._retry_budget = retry_budget
        self._user_agent = user_agent
        self._messages = host.messages if host.messages is not None else HostMessages.generic(host.name)

    @property
    def host(self) -> EgressHost:
        """The host this client is bound to."""
        return self._host

    @property
    def origin(self) -> str:
        """`scheme://host[:port]` this client talks to; one origin has exactly one owner."""
        return self._origin

    @property
    def circuit_state(self) -> CircuitState:
        """State of this host's breaker; reading it has no side effects."""
        return self._circuit_breaker.state

    async def _enforce_origin(self, request: httpx.Request) -> None:
        """Request hook: refuse any hop that would leave this client's own origin.

        `httpx` follows redirects inside its own loop, below the limiter, the slot and the
        breaker, and calls this hook for every hop. A hop to another host would reach it
        unpaced, carrying the query string and this client's identity, and would bypass that
        host's own controls. Raising here stops the request before it leaves; the exception is
        not an `httpx.HTTPError`, so the retry loop neither retries nor books it as a breaker
        failure.
        """
        if _origin_of(request.url) != self._origin:
            raise EgressRedirectRefusedError(self._messages.redirect_refused, source=self._host.name)

    async def start(self) -> None:
        """Initialize the HTTP client."""
        if self._client is None:
            hooks: list[Callable[[httpx.Request], Awaitable[None]]] = []
            if self._host.pin_origin:
                hooks.append(self._enforce_origin)
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=self._timeout, write=10.0, pool=10.0),
                headers={
                    "User-Agent": self._user_agent,
                    "Accept": "application/json",
                    **self._host.headers,
                },
                follow_redirects=True,
                max_redirects=self._host.max_redirects,
                verify=self._host.verify,
                event_hooks={"request": hooks},
            )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _receive_within(self, response: httpx.Response, max_bytes: int) -> httpx.Response:
        """Read a streamed body, giving up as soon as it is known to exceed `max_bytes`.

        The budget is spent on *decoded* bytes, because that is what the document store
        and the converter hold. `aiter_bytes()` applies the content codec as it goes, so
        the running total is already in those units; `Content-Length`, which counts the
        bytes on the wire, is only an exact answer when nothing was encoded, and is
        therefore consulted only then. A compressed body is settled by the stream check
        instead — one chunk later, never wrongly.

        Every non-2xx status is read in full (their bodies are small and the translating
        layer quotes them) and left to `raise_for_status`, exactly as the non-streaming
        path does, so streaming changes nothing about how they are judged.

        The materialised body is handed back as a fresh `Response` built from the
        original headers, which is what keeps the charset-driven `.text` decoding
        identical to the non-streaming path. `Content-Encoding` and the wire
        `Content-Length` are dropped from that copy: the body handed over has already
        been decoded, so carrying them would make `httpx` decode it a second time and
        raise `DecodingError` on every compressed act.
        """
        if not response.is_success:
            await response.aread()
            response.raise_for_status()

        encoding = response.headers.get("content-encoding", "").strip().lower()
        declared = response.headers.get("content-length")
        if encoding in ("", "identity") and declared is not None and declared.isdigit() and int(declared) > max_bytes:
            raise ResponseTooLargeError(str(response.url), int(declared), max_bytes, exact=True)

        chunks: list[bytes] = []
        received = 0
        async for chunk in response.aiter_bytes():
            received += len(chunk)
            if received > max_bytes:
                raise ResponseTooLargeError(str(response.url), received, max_bytes, exact=False)
            chunks.append(chunk)

        carried = [
            (name, value)
            for name, value in response.headers.raw
            if name.lower() not in (b"content-encoding", b"content-length")
        ]
        return httpx.Response(
            response.status_code,
            headers=carried,
            content=b"".join(chunks),
            request=response.request,
        )

    async def _send(
        self,
        method: str,
        path: str,
        *,
        request_class: RequestClass = RequestClass.LIGHT,
        deadline: float | None = None,
        max_bytes: int | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a single HTTP request.

        Lowest layer: builds the URL, waits its turn and guards the class semaphore. It
        neither retries nor translates errors, and every error it raises about the wire
        is an `httpx` one — the exceptions are its own pacing refusal below and the
        byte-budget refusal, both of which are this client's policy, not the wire's.

        `max_bytes` switches the request to a streamed read bounded by that budget
        (see `_receive_within`); `None` reads the whole body as before.

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
            ResponseTooLargeError: The body exceeds `max_bytes`. Not an `httpx` error
                for the same reason — a retry would only download the same body again.
        """
        if self._client is None:
            await self.start()

        assert self._client is not None  # ensured by start()

        url = f"{self._base_url}/{path.lstrip('/')}"

        await self._rate_limiter.acquire(deadline=deadline)

        async with self._semaphores[request_class]:
            if max_bytes is None:
                response = await self._client.request(method, url, **kwargs)
                response.raise_for_status()
                return response
            # The stream is consumed inside the slot on purpose: a heavy download
            # holds its class semaphore for as long as bytes are still arriving.
            async with self._client.stream(method, url, **kwargs) as streamed:
                return await self._receive_within(streamed, max_bytes)

    async def _execute_with_resilience(
        self,
        method: str,
        path: str,
        *,
        request_class: RequestClass = RequestClass.LIGHT,
        max_bytes: int | None = None,
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
            ResponseTooLargeError: When the body exceeds `max_bytes`. Passes through on
                the same terms: neither retried nor booked against the breaker.
            httpx.HTTPError: The last error once attempts or budget run out.
        """
        deadline = self._clock() + self._retry_budget
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
                    self._messages.breaker_open,
                    status_code=503,
                    source=self._host.name,
                )

            try:
                response = await self._send(
                    method, path, request_class=request_class, deadline=deadline, max_bytes=max_bytes, **kwargs
                )
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
                    or self._clock() + delay >= deadline
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
        raise ApiUnavailableError(
            f"{self._host.name} nie odpowiedziało w ramach dozwolonych prób",
            status_code=503,
            source=self._host.name,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        request_class: RequestClass = RequestClass.LIGHT,
        max_bytes: int | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Run a request and translate `httpx` errors into domain exceptions.

        Topmost layer. Translation happens outside the retry loop, so the policy
        always judges the original exception type.

        Args:
            method: HTTP method.
            path: Path relative to the host's base URL.
            max_bytes: Byte budget for the body; `None` reads it whole.
            **kwargs: Additional httpx request parameters.

        Returns:
            HTTP response.

        Raises:
            ActNotFoundError: When the resource does not exist (404).
            ApiUnavailableError: When the API returned 5xx, transport failed,
                or the circuit breaker is open.
            ResponseTooLargeError: When the body exceeds `max_bytes`. Already a
                domain exception, so it needs no translation here.
            SejmApiError: For the remaining request-side errors.
        """
        try:
            return await self._execute_with_resilience(
                method, path, request_class=request_class, max_bytes=max_bytes, **kwargs
            )
        except EgressPaceDeadlineError as exc:
            # Reported as unavailability, which is what it is from the caller's side —
            # but the message says who is holding the traffic back, so an agent is not
            # told "the source is down" when the truth is "we are keeping the pace we
            # promised". `max(1, ...)` because a sub-second wait floored to "~0 s" reads
            # as "no wait at all" and invites an immediate retry. The two causes get
            # different sentences: only a server-requested pause is a halt — the other
            # is this client keeping the pace it promised, and saying "halted" there
            # would describe a condition that does not exist.
            seconds = max(1, round(exc.wait))
            template = self._messages.pacing_paused if exc.paused else self._messages.pacing_queued
            raise ApiUnavailableError(
                template.format(seconds=seconds), status_code=503, source=self._host.name
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            url = str(exc.request.url)
            if status == 404:
                raise ActNotFoundError(path, source=self._host.name) from exc
            if 500 <= status <= 599:
                raise ApiUnavailableError(
                    self._messages.upstream_status.format(status=status),
                    status_code=status,
                    url=url,
                    source=self._host.name,
                ) from exc
            raise SejmApiError(
                f"HTTP {status}: {exc.response.text}",
                status_code=status,
                url=url,
                source=self._host.name,
            ) from exc
        except httpx.TransportError as exc:
            # `str(exc)` is httpx-authored and can quote hosts, proxies or errno
            # text. The caller gets the failure class and the project-built
            # endpoint; the detail stays on `__cause__` for the DEBUG log (#61).
            raise ApiUnavailableError(
                self._messages.transport_failure.format(error=type(exc).__name__, method=method, path=path),
                source=self._host.name,
            ) from exc
        except httpx.HTTPError as exc:
            raise SejmApiError(self._messages.request_error.format(detail=exc), source=self._host.name) from exc

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
            # The origin prefix keeps two hosts from ever sharing an entry for the same path.
            cache_key = f"{self._origin}|json:{path}:{params or {}}"
            cached = await self._cache.get(cache_key, max_age=cache_ttl)
            if cached is not None:
                return cached

        response = await self._request("GET", path, params=params)
        data = response.json()

        if cache_key is not None and cache_ttl is not None:
            await self._cache.set(cache_key, data, cache_ttl)

        return data

    async def get_text(self, path: str, *, max_bytes: int | None = None) -> str:
        """Get text response from API.

        Args:
            path: URL path
            max_bytes: Byte budget for the body, enforced while it streams in;
                `None` reads it whole.

        Returns:
            Response text

        Raises:
            ResponseTooLargeError: The body exceeds `max_bytes`.
        """
        response = await self._request(
            "GET",
            path,
            request_class=RequestClass.HEAVY,
            max_bytes=max_bytes,
            headers={"Accept": "text/html, text/plain, */*"},
        )
        return response.text

    async def get_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        """Get binary response from API.

        Args:
            path: URL path
            max_bytes: Byte budget for the body, enforced while it streams in;
                `None` reads it whole.

        Returns:
            Response bytes

        Raises:
            ResponseTooLargeError: The body exceeds `max_bytes`.
        """
        response = await self._request(
            "GET",
            path,
            request_class=RequestClass.HEAVY,
            max_bytes=max_bytes,
            headers={"Accept": "application/pdf, application/octet-stream, */*"},
        )
        return response.content


@dataclass(frozen=True, slots=True)
class EgressDefaults:
    """The values every host built by the hub starts from.

    Field names equal the matching `SejmApiClient` keywords. The lifespan fills them from the
    settings, so `client/` never imports `config`. A module may ask for a lower rate or burst
    than these, never a higher one.
    """

    timeout: float = 30.0
    max_concurrent: int = 8
    max_concurrent_content: int = 2
    rate_per_second: float = 5.0
    rate_burst: int = 10
    max_server_pause: float = 60.0
    max_attempts: int = 3
    retry_budget: float = 45.0
    user_agent: str = DEFAULT_USER_AGENT
    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    half_open_max_calls: int = 3


@dataclass(frozen=True, slots=True)
class SourceState:
    """What the health route may say about one source: its name and breaker state, nothing else."""

    name: str
    circuit_state: CircuitState


class EgressHub:
    """Registry of every upstream host this process talks to.

    A module asks the hub for one `EgressClient` per host and gets a client with its own
    limiter, breaker, concurrency lanes and connection pool, so nothing one host does can slow
    or break another. Only the `TTLCache` object is shared, and only through keys prefixed with
    the host origin. The hub also decides what is "active": a host is active exactly when it
    has been registered here.
    """

    def __init__(
        self,
        cache: TTLCache,
        defaults: EgressDefaults | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._cache = cache
        self._defaults = defaults if defaults is not None else EgressDefaults()
        self._clock = clock
        # Registration order is the order `/health` lists sources in.
        self._clients: dict[str, EgressClient] = {}
        self._owned: list[EgressClient] = []

    def _claim(self, client_origin: str, name: str) -> None:
        if client_origin in self._clients:
            raise EgressConfigError(
                f"Adres {client_origin} ma już właściciela ({self._clients[client_origin].host.name}); "
                "jedno źródło obsługuje jeden adres."
            )
        if any(known.host.name == name for known in self._clients.values()):
            raise EgressConfigError(f"Nazwa źródła {name} jest już zajęta.")

    def client_for(self, host: EgressHost, caller: CallerIdentity) -> EgressClient:
        """Build and register the client a module uses for one host.

        Raises:
            EgressConfigError: The origin or the name is taken, the host asks for a higher rate
                or burst than the defaults, or it opts out of the origin pin or asks for more
                than `MAX_MODULE_REDIRECTS` redirect hops.
        """
        defaults = self._defaults
        if not host.pin_origin or host.max_redirects > MAX_MODULE_REDIRECTS:
            raise EgressConfigError(
                f"Źródło {host.name}: moduł nie może wyłączyć przypięcia adresu ani przekroczyć "
                f"{MAX_MODULE_REDIRECTS} przekierowań."
            )
        if host.rate_per_second is not None and host.rate_per_second > defaults.rate_per_second:
            raise EgressConfigError(f"Źródło {host.name}: tempo żądań nie może przekraczać {defaults.rate_per_second}.")
        if host.rate_burst is not None and host.rate_burst > defaults.rate_burst:
            raise EgressConfigError(
                f"Źródło {host.name}: pojemność kubełka nie może przekraczać {defaults.rate_burst}."
            )
        client_origin = _origin_of(httpx.URL(host.base_url))
        self._claim(client_origin, host.name)

        client = EgressClient(
            host=host,
            cache=self._cache,
            timeout=defaults.timeout,
            max_concurrent=defaults.max_concurrent,
            circuit_breaker=CircuitBreaker(
                failure_threshold=defaults.failure_threshold,
                recovery_timeout=defaults.recovery_timeout,
                half_open_max_calls=defaults.half_open_max_calls,
                name=host.name,
            ),
            max_attempts=defaults.max_attempts,
            retry_budget=defaults.retry_budget,
            user_agent=f"{defaults.user_agent} {caller.product}",
            max_concurrent_content=defaults.max_concurrent_content,
            rate_per_second=host.rate_per_second if host.rate_per_second is not None else defaults.rate_per_second,
            rate_burst=host.rate_burst if host.rate_burst is not None else defaults.rate_burst,
            max_server_pause=defaults.max_server_pause,
            clock=self._clock,
        )
        self._clients[client_origin] = client
        self._owned.append(client)
        return client

    def adopt(self, client: EgressClient) -> None:
        """Register a client built elsewhere (the Sejm's), so its origin cannot be claimed twice.

        The hub never closes an adopted client; whoever built it does.

        Raises:
            EgressConfigError: The origin or the name is already registered.
        """
        self._claim(client.origin, client.host.name)
        self._clients[client.origin] = client

    def source_states(self) -> tuple[SourceState, ...]:
        """Name and breaker state of every registered host, in registration order.

        Reading the breaker's `state` has no side effects, so a health probe never moves a
        breaker.
        """
        return tuple(SourceState(client.host.name, client.circuit_state) for client in self._clients.values())

    async def close(self) -> None:
        """Close the clients created by `client_for`, whatever happens to any single one.

        Raises:
            ExceptionGroup: Every error the closes raised, after all of them were attempted.
        """
        owned, self._owned = self._owned, []
        errors: list[Exception] = []
        for client in owned:
            try:
                await client.close()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("Nie udało się zamknąć wszystkich klientów źródeł.", errors)


__all__ = [
    "DEFAULT_USER_AGENT",
    "MAX_MODULE_REDIRECTS",
    "SEJM_MESSAGES",
    "CallerIdentity",
    "EgressClient",
    "EgressDefaults",
    "EgressHost",
    "EgressHub",
    "HostMessages",
    "RequestClass",
    "SourceState",
]
