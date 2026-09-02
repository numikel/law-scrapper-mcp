"""Token bucket protecting the server from its own clients (F26)."""

from __future__ import annotations

import asyncio

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from law_scrapper_mcp.http.rate_limit import ExemptPathCredentialStripper, RateLimitMiddleware


async def _ok(_request):
    return JSONResponse({"ok": True})


def build_client(*, client: tuple[str, int] = ("127.0.0.1", 50000), **overrides) -> TestClient:
    kwargs = {"requests": 5, "window": 60.0, "burst": 5, "trusted_proxies": []}
    kwargs.update(overrides)
    inner = Starlette(routes=[Route("/mcp", _ok, methods=["GET"]), Route("/health", _ok)])
    # ASGITransport's default peer is the literal string "testclient", not an
    # IP — real uvicorn deployments populate scope["client"] with an actual
    # address. Pin a loopback IP so tests exercising `trusted_proxies` CIDR
    # matching see something that can actually be parsed as an IP.
    return TestClient(RateLimitMiddleware(inner, **kwargs), client=client)


def test_requests_within_the_bucket_pass() -> None:
    client = build_client()
    assert [client.get("/mcp").status_code for _ in range(5)] == [200] * 5


def test_exhausted_bucket_returns_429_with_retry_after() -> None:
    """Criterion 10 (with BURST=5, see P2)."""
    client = build_client()
    for _ in range(5):
        client.get("/mcp")
    response = client.get("/mcp")
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1


def test_bucket_refills_over_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refill is driven by a monotonic clock, so the test moves the clock."""
    from law_scrapper_mcp.http import rate_limit

    now = [1000.0]
    monkeypatch.setattr(rate_limit, "monotonic", lambda: now[0])
    client = build_client(requests=60, window=60.0, burst=1)
    assert client.get("/mcp").status_code == 200
    assert client.get("/mcp").status_code == 429
    now[0] += 2.0  # 2 s at 1 token/s
    assert client.get("/mcp").status_code == 200


def test_health_is_exempt_from_rate_limit() -> None:
    """Criterion 16 (D12): the container probe must not share the budget.

    The probe in docker-compose.yml runs inside the container, so it arrives
    from the loopback — that is the peer the exemption exists for.
    """
    client = build_client(requests=1, burst=1)
    assert [client.get("/health").status_code for _ in range(10)] == [200] * 10


def test_health_from_ipv6_loopback_is_exempt() -> None:
    client = build_client(requests=1, burst=1, client=("::1", 50000))
    assert [client.get("/health").status_code for _ in range(10)] == [200] * 10


def test_health_from_a_non_loopback_peer_is_metered() -> None:
    """#39: a blanket exemption made `/health` an unmetered route for anyone.

    Only the container's own probe needs the exemption; a remote peer looping
    over `/health` gets the same per-client bucket as every other request.
    """
    client = build_client(requests=1, burst=1, client=("203.0.113.5", 5000))
    assert client.get("/health").status_code == 200
    assert client.get("/health").status_code == 429


def test_health_without_a_client_address_is_metered() -> None:
    """A scope with no `client` cannot prove it is local, so it is not exempt."""
    middleware = RateLimitMiddleware(
        Starlette(routes=[Route("/health", _ok)]),
        requests=1,
        window=60.0,
        burst=1,
        trusted_proxies=[],
    )
    statuses: list[int] = []

    async def send(message) -> None:
        if message["type"] == "http.response.start":
            statuses.append(message["status"])

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def drive() -> None:
        for _ in range(2):
            scope = {"type": "http", "method": "GET", "path": "/health", "headers": [], "query_string": b""}
            await middleware(scope, receive, send)

    asyncio.run(drive())

    assert statuses == [200, 429]


def test_xff_from_untrusted_peer_is_ignored() -> None:
    """Criterion 11: an unverified header must not become the bucket key,
    or the limit is bypassed by one line of curl."""
    client = build_client(burst=1)
    assert client.get("/mcp", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 200
    assert client.get("/mcp", headers={"X-Forwarded-For": "5.6.7.8"}).status_code == 429


def test_xff_from_trusted_peer_splits_the_buckets() -> None:
    """TestClient presents itself as testclient/127.0.0.1."""
    client = build_client(burst=1, trusted_proxies=["127.0.0.0/8"])
    assert client.get("/mcp", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 200
    assert client.get("/mcp", headers={"X-Forwarded-For": "5.6.7.8"}).status_code == 200
    assert client.get("/mcp", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 429


def test_idle_buckets_are_evicted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The counter itself must not become the memory-exhaustion vector."""
    from law_scrapper_mcp.http import rate_limit

    now = [1000.0]
    monkeypatch.setattr(rate_limit, "monotonic", lambda: now[0])
    middleware = RateLimitMiddleware(
        Starlette(routes=[Route("/mcp", _ok, methods=["GET"])]),
        requests=5,
        window=60.0,
        burst=5,
        trusted_proxies=[],
    )
    first_client = TestClient(middleware, client=("127.0.0.1", 50000))
    first_client.get("/mcp")
    assert middleware._buckets.keys() == {"127.0.0.1"}
    now[0] += 121.0  # beyond two windows
    # A request from a DIFFERENT client key drives the next eviction sweep.
    # Reusing the original key would pass even with a no-op `_evict_idle`,
    # since re-touching the bucket keeps it alive either way.
    second_client = TestClient(middleware, client=("10.0.0.1", 50000))
    second_client.get("/mcp")
    assert middleware._buckets.keys() == {"10.0.0.1"}


def test_non_http_scope_passes_through() -> None:
    """Lifespan and websocket scopes carry no client address."""
    client = build_client()
    with client:  # entering the context manager runs the lifespan scope
        assert client.get("/mcp").status_code == 200


def build_header_spy_client(**overrides) -> tuple[TestClient, list[list[str]]]:
    """A client whose inner app records the header names it was handed."""
    seen: list[list[str]] = []

    async def _record(request):
        seen.append(sorted(request.headers.keys()))
        return JSONResponse({"ok": True})

    kwargs = {"requests": 5, "window": 60.0, "burst": 5, "trusted_proxies": []}
    kwargs.update(overrides)
    inner = Starlette(routes=[Route("/mcp", _record, methods=["GET"]), Route("/health", _record)])
    return TestClient(RateLimitMiddleware(inner, **kwargs), client=("127.0.0.1", 50000)), seen


def build_stripper_client() -> tuple[TestClient, list[list[str]]]:
    """The stripper alone, as `build_http_app()` installs it: outermost, always."""
    seen: list[list[str]] = []

    async def _record(request):
        seen.append(sorted(request.headers.keys()))
        return JSONResponse({"ok": True})

    inner = Starlette(routes=[Route("/mcp", _record, methods=["GET"]), Route("/health", _record)])
    return TestClient(ExemptPathCredentialStripper(inner), client=("127.0.0.1", 50000)), seen


def test_health_reaches_the_app_without_the_authorization_header() -> None:
    """`/health` is exempt from the limiter, so it must not reach the verifier.

    The SDK mounts its authentication backend as application-level middleware,
    so a token presented here would be verified on the one route with no request
    budget — in `oauth` mode an unknown `kid` makes PyJWKClient refetch the key
    set, turning an anonymous loop over `/health` into unmetered outbound
    traffic against the operator's identity provider.
    """
    client, seen = build_stripper_client()

    response = client.get("/health", headers={"Authorization": "Bearer some.jwt.value"})

    assert response.status_code == 200
    assert "authorization" not in seen[0]


def test_mcp_keeps_the_authorization_header() -> None:
    """Stripping is confined to exempt paths — the counter-test to the above."""
    client, seen = build_stripper_client()

    client.get("/mcp", headers={"Authorization": "Bearer some.jwt.value"})

    assert "authorization" in seen[0]


def test_rate_limiter_alone_does_not_strip() -> None:
    """The limiter exempts `/health`; removing the credential is not its job.

    Recorded so the split stays deliberate: the strip was moved out precisely
    because living here made it conditional on `LAW_MCP_RATE_LIMIT_ENABLED`.
    """
    client, seen = build_header_spy_client()

    client.get("/health", headers={"Authorization": "Bearer some.jwt.value"})

    assert "authorization" in seen[0]


def test_non_address_forwarded_for_falls_back_to_the_peer() -> None:
    """A forwarded value that is not an address must not become a bucket key.

    A proxy that forwards the caller's own header instead of appending to it
    would otherwise let one client mint an unbounded number of buckets, each
    keyed by a string it chooses. Sharing the peer's bucket throttles harder
    than intended, which is the safe direction to be wrong in.
    """
    middleware = RateLimitMiddleware(
        Starlette(routes=[Route("/mcp", _ok, methods=["GET"])]),
        requests=5,
        window=60.0,
        burst=5,
        trusted_proxies=["127.0.0.0/8"],
    )
    client = TestClient(middleware, client=("127.0.0.1", 50000))

    for junk in ("not-an-address", "a" * 400, "unknown"):
        client.get("/mcp", headers={"X-Forwarded-For": junk})

    assert middleware._buckets.keys() == {"127.0.0.1"}


def test_duplicate_forwarded_for_headers_use_the_last_one() -> None:
    """A caller's own header must not outrank the one the proxy appended.

    A proxy that appends rather than edits produces a second `X-Forwarded-For`
    header, so reading the first match would hand the bucket key straight to
    the caller — an address it chose, which parses fine and so survives the
    address check.
    """
    middleware = RateLimitMiddleware(
        Starlette(routes=[Route("/mcp", _ok, methods=["GET"])]),
        requests=5,
        window=60.0,
        burst=5,
        trusted_proxies=["127.0.0.0/8"],
    )
    client = TestClient(middleware, client=("127.0.0.1", 50000))

    # httpx sends one header per tuple, preserving order.
    client.get(
        "/mcp",
        headers=[("X-Forwarded-For", "9.9.9.9"), ("X-Forwarded-For", "203.0.113.5")],
    )

    assert middleware._buckets.keys() == {"203.0.113.5"}


def test_address_forwarded_for_still_becomes_the_key() -> None:
    """The counter-test: a real address from a trusted peer is still honoured."""
    middleware = RateLimitMiddleware(
        Starlette(routes=[Route("/mcp", _ok, methods=["GET"])]),
        requests=5,
        window=60.0,
        burst=5,
        trusted_proxies=["127.0.0.0/8"],
    )
    client = TestClient(middleware, client=("127.0.0.1", 50000))

    client.get("/mcp", headers={"X-Forwarded-For": "203.0.113.7"})

    assert middleware._buckets.keys() == {"203.0.113.7"}
