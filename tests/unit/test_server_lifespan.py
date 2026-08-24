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
