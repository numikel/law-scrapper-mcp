"""`get_json(none_on_no_content=True)`: a host that answers 204 for "no such resource"."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
import respx

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.sejm_client import SejmApiClient

URL = "https://api.sejm.gov.pl/eli/acts/DU/2025/9"


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[SejmApiClient]:
    api = SejmApiClient(cache=TTLCache(max_entries=10), max_attempts=1)
    try:
        yield api
    finally:
        await api.close()


@pytest.mark.asyncio
@respx.mock
async def test_204_with_the_keyword_returns_none(client: SejmApiClient) -> None:
    respx.get(URL).mock(return_value=httpx.Response(204))

    assert await client.get_json("acts/DU/2025/9", none_on_no_content=True) is None


@pytest.mark.asyncio
@respx.mock
async def test_204_is_not_cached(client: SejmApiClient) -> None:
    """A missing act can be published later, so the "nothing" answer must not be remembered."""
    route = respx.get(URL).mock(return_value=httpx.Response(204))

    await client.get_json("acts/DU/2025/9", cache_ttl=600, none_on_no_content=True)
    await client.get_json("acts/DU/2025/9", cache_ttl=600, none_on_no_content=True)
    assert route.call_count == 2

    route.mock(return_value=httpx.Response(200, json={"title": "now there"}))
    assert await client.get_json("acts/DU/2025/9", cache_ttl=600, none_on_no_content=True) == {"title": "now there"}


@pytest.mark.asyncio
@respx.mock
async def test_204_without_the_keyword_behaves_as_before(client: SejmApiClient) -> None:
    """The default is byte-identical to 4.4.2: an empty body still fails to parse."""
    respx.get(URL).mock(return_value=httpx.Response(204))

    with pytest.raises(ValueError):
        await client.get_json("acts/DU/2025/9")


@pytest.mark.asyncio
@respx.mock
async def test_the_keyword_changes_nothing_for_a_200(client: SejmApiClient) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json={"title": "x"}))

    assert await client.get_json("acts/DU/2025/9", none_on_no_content=True) == {"title": "x"}
