"""The shared fixtures in tests/conftest.py must mirror the production wiring."""

from __future__ import annotations

import pytest

from law_scrapper_mcp.client.sejm_client import RequestClass, SejmApiClient
from law_scrapper_mcp.config import settings

pytestmark = pytest.mark.asyncio


async def test_mock_client_uses_the_production_concurrency_lanes(mock_client: SejmApiClient) -> None:
    """Both semaphore lanes are sized from `Settings`, exactly as `server.py` sizes them.

    A literal in the fixture would let the suite exercise a wider (or
    narrower) client than the one that ships, so every concurrency test on
    top of `mock_client` would be reasoning about a different budget.
    """
    light = mock_client._semaphores[RequestClass.LIGHT]._value
    heavy = mock_client._semaphores[RequestClass.HEAVY]._value

    assert (light, heavy) == (settings.api_max_concurrent, settings.api_max_concurrent_content)
    assert mock_client._timeout == settings.api_timeout
