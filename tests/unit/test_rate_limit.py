"""Token bucket protecting the server from its own clients (F26)."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from law_scrapper_mcp.http.rate_limit import RateLimitMiddleware


async def _ok(_request):
    return JSONResponse({"ok": True})


def build_client(**overrides) -> TestClient:
    kwargs = {"requests": 5, "window": 60.0, "burst": 5, "trusted_proxies": []}
    kwargs.update(overrides)
    inner = Starlette(routes=[Route("/mcp", _ok, methods=["GET"]), Route("/health", _ok)])
    # ASGITransport's default peer is the literal string "testclient", not an
    # IP — real uvicorn deployments populate scope["client"] with an actual
    # address. Pin a loopback IP so tests exercising `trusted_proxies` CIDR
    # matching see something that can actually be parsed as an IP.
    return TestClient(RateLimitMiddleware(inner, **kwargs), client=("127.0.0.1", 50000))


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
    """Criterion 16 (D12): the container probe must not share the budget."""
    client = build_client(requests=1, burst=1)
    assert [client.get("/health").status_code for _ in range(10)] == [200] * 10


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
