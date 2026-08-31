"""Per-client token bucket for the inbound HTTP surface (F26).

Written by hand rather than pulled from slowapi: that library is built around
FastAPI route decorators, and the app protected here is assembled by the SDK,
with no routes of ours to decorate.

Scope note: this is the inbound direction — the server protected from its
clients. Outbound politeness towards api.sejm.gov.pl is a separate budget in
`SejmApiClient` (cluster 8) and must not be conflated with this one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from math import ceil
from time import monotonic

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# A hardcoded list, not a setting: making it configurable would allow an
# operator to put the container healthcheck under the limit, which is precisely
# the outage this exemption prevents (D12).
EXEMPT_PATHS = frozenset({"/health"})


@dataclass
class _Bucket:
    tokens: float
    last_seen: float


class RateLimitMiddleware:
    """Limits requests per client key, refilling continuously.

    State is per process. With several replicas the effective limit multiplies
    by the replica count — irrelevant for the current single-process deployment,
    recorded here so it does not surprise anyone who scales it out.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        requests: int,
        window: float,
        burst: int,
        trusted_proxies: Sequence[str],
    ) -> None:
        self._app = app
        self._rate = requests / window
        self._capacity = float(burst)
        self._idle_ttl = window * 2
        self._trusted = [ip_network(entry, strict=False) for entry in trusted_proxies]
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        if scope.get("path") in EXEMPT_PATHS:
            await self._app(_without_authorization(scope), receive, send)
            return

        allowed, retry_after = await self._consume(self._client_key(scope))
        if allowed:
            await self._app(scope, receive, send)
            return

        response = JSONResponse(
            {"error": "Przekroczono limit żądań. Spróbuj ponownie później."},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
        await response(scope, receive, send)

    async def _consume(self, key: str) -> tuple[bool, int]:
        async with self._lock:
            now = monotonic()
            self._evict_idle(now)
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=self._capacity, last_seen=now)
                self._buckets[key] = bucket
            else:
                elapsed = now - bucket.last_seen
                bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._rate)
                bucket.last_seen = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0
            return False, max(1, ceil((1.0 - bucket.tokens) / self._rate))

    def _evict_idle(self, now: float) -> None:
        stale = [key for key, bucket in self._buckets.items() if now - bucket.last_seen > self._idle_ttl]
        for key in stale:
            del self._buckets[key]

    def _client_key(self, scope: Scope) -> str:
        client = scope.get("client")
        peer = client[0] if client else "unknown"
        if not self._is_trusted(peer):
            return peer
        # Every occurrence, not the first match. A caller may send its own
        # `X-Forwarded-For`, and a proxy appending to it produces a second
        # header rather than editing the first; reading the first would then
        # hand the caller the key. The proxy's own contribution is last.
        values = [value for name, value in scope.get("headers", []) if name == b"x-forwarded-for"]
        if values:
            # Last entry, per spec §4.5. With a single trusted proxy — the
            # deployment this exists for — that entry is the client.
            forwarded = values[-1].decode("latin-1").split(",")[-1].strip()
            # Only an address may become a bucket key. A proxy that forwards
            # the client's own header instead of appending to it would
            # otherwise let a caller mint an unbounded number of buckets,
            # each keyed by a string it chooses and sized up to the header
            # limit. Falling back to the peer shares the proxy's bucket,
            # which throttles harder than intended rather than less.
            if _is_ip_address(forwarded):
                return forwarded
        return peer

    def _is_trusted(self, peer: str) -> bool:
        if not self._trusted:
            return False
        try:
            address = ip_address(peer)
        except ValueError:
            return False
        return any(address in network for network in self._trusted)


def _is_ip_address(value: str) -> bool:
    """Whether a forwarded-for entry is an address rather than free text."""
    try:
        ip_address(value)
    except ValueError:
        return False
    return True


def _without_authorization(scope: Scope) -> Scope:
    """Strip the credential from a request on an exempt path.

    Exemption from the limiter and exemption from the token verifier are two
    different boundaries, and the SDK draws them differently: it mounts its
    authentication backend as application-level middleware, so a token presented
    on `/health` is verified even though `RequireAuthMiddleware` guards only
    `/mcp`. That put the verifier on the one route with no request budget. In
    `oauth` mode an unrecognised `kid` makes PyJWKClient refetch the whole key
    set, so an anonymous loop over `/health` became unmetered outbound traffic
    against the operator's identity provider — the denial of service F26 exists
    to prevent, entering through the path F26 deliberately exempts.

    Nothing behind `/health` reads the header, so dropping it costs nothing.
    """
    headers = scope.get("headers", [])
    kept = [(name, value) for name, value in headers if name != b"authorization"]
    if len(kept) == len(headers):
        return scope
    return {**scope, "headers": kept}
