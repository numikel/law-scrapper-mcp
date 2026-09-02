"""Regression guard for lifespan cleanup on an unclean exit."""

from __future__ import annotations

import asyncio

import pytest

from law_scrapper_mcp import server as server_module
from law_scrapper_mcp.client import cache as cache_module
from law_scrapper_mcp.server import lifespan

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def clean_health_state():
    """Module-level state must not leak between tests."""
    server_module._health_state.clear()
    yield
    server_module._health_state.clear()


async def test_lifespan_populates_the_health_handle() -> None:
    """The bridge from D4 must actually be built."""
    async with lifespan(server_module.app):
        snapshot = server_module._health_state.snapshot()

    assert snapshot["circuit_state"] == "closed"
    assert snapshot["failure_count"] == 0


async def test_cleanup_survives_cancellation(monkeypatch) -> None:
    """`CancelledError` is the default SIGTERM path inside a container.

    The `Exception` path has been covered since `392864e`
    (`test_server.py:130`); the `BaseException`/`CancelledError` path was not.
    """
    cleared: list[str] = []
    original_clear = cache_module.TTLCache.clear

    async def spy_clear(self) -> None:
        cleared.append("cache")
        await original_clear(self)

    monkeypatch.setattr(cache_module.TTLCache, "clear", spy_clear)

    httpx_client = None
    with pytest.raises(asyncio.CancelledError):
        async with lifespan(server_module.app) as context:
            httpx_client = context.client._client
            raise asyncio.CancelledError()

    assert httpx_client is not None
    assert httpx_client.is_closed is True
    assert cleared == ["cache"]
    assert server_module._health_state.snapshot() == {"circuit_state": "unknown"}


async def test_cleanup_finishes_when_closing_the_client_raises(monkeypatch) -> None:
    """The `finally` must not stop halfway when `client.close()` raises (#31).

    Ordering under test: the health handle is cleared first, so `/health`
    cannot claim a live breaker after the lifespan has ended; the cache is
    cleared even when closing the HTTP client fails, so a failed close does
    not leave a populated cache behind in a process that is about to restart.
    The exception itself still propagates — swallowing it would hide the
    reason the shutdown was unclean.
    """
    cleared: list[str] = []
    original_clear = cache_module.TTLCache.clear

    async def spy_clear(self) -> None:
        cleared.append("cache")
        await original_clear(self)

    async def failing_close(self) -> None:
        raise RuntimeError("transport already gone")

    monkeypatch.setattr(cache_module.TTLCache, "clear", spy_clear)
    monkeypatch.setattr(server_module.SejmApiClient, "close", failing_close)

    with pytest.raises(RuntimeError, match="transport already gone"):
        async with lifespan(server_module.app):
            pass

    assert server_module._health_state.snapshot() == {"circuit_state": "unknown"}
    assert cleared == ["cache"]
