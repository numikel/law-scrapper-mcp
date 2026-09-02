"""`get_json` shares cache entries across callers but bounds each read by the caller's TTL."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
import respx

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.sejm_client import SejmApiClient

BASE = "https://api.sejm.gov.pl/eli/"
PARAMS = {"publisher": "DU", "dateFrom": "2024-01-01", "dateTo": "2024-01-31", "limit": 20}


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[SejmApiClient]:
    api = SejmApiClient(cache=TTLCache(max_entries=10), max_attempts=1)
    try:
        yield api
    finally:
        await api.close()


@pytest.mark.asyncio
@respx.mock
async def test_a_shorter_ttl_refuses_an_entry_a_longer_ttl_caller_wrote(client: SejmApiClient) -> None:
    """The same `acts/search` question from two tools shares one entry (D8), yet a
    caller with a 300 s TTL must not be served the 600 s caller's 400 s old page."""
    route = respx.get(f"{BASE}acts/search").mock(return_value=httpx.Response(200, json={"items": [], "totalCount": 0}))
    with patch("time.time") as clock:
        clock.return_value = 1_000.0
        await client.get_json("acts/search", params=PARAMS, cache_ttl=600)
        clock.return_value = 1_400.0
        await client.get_json("acts/search", params=PARAMS, cache_ttl=300)
        assert route.call_count == 2, "the 300 s caller must refetch a 400 s old entry"
        clock.return_value = 1_500.0
        await client.get_json("acts/search", params=PARAMS, cache_ttl=600)
        assert route.call_count == 2, "the refreshed entry serves the 600 s caller again"
