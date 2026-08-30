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
        if scope["type"] != "http" or scope.get("path") in EXEMPT_PATHS:
            await self._app(scope, receive, send)
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
        for name, value in scope.get("headers", []):
            if name == b"x-forwarded-for":
                # Last entry, per spec §4.5. With a single trusted proxy — the
                # deployment this exists for — that entry is the client.
                forwarded = value.decode("latin-1").split(",")[-1].strip()
                if forwarded:
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
