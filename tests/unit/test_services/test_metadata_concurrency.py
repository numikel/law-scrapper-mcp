"""Concurrency, ordering and cache behaviour of metadata fetching."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx
from httpx import Response

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.models.enums import MetadataCategory
from law_scrapper_mcp.services.metadata_service import MetadataService

pytestmark = pytest.mark.asyncio

ENDPOINTS = ("keywords", "acts", "statuses", "types", "institutions")
CATEGORY_ORDER = ("keywords", "publishers", "statuses", "types", "institutions")


def _url(endpoint: str) -> str:
    return f"https://api.sejm.gov.pl/eli/{endpoint}"


async def _client(max_concurrent: int = 10) -> SejmApiClient:
    client = SejmApiClient(cache=TTLCache(max_entries=100), timeout=5.0, max_concurrent=max_concurrent)
    await client.start()
    return client


async def test_all_five_categories_are_fetched_concurrently() -> None:
    """A barrier of five deadlocks unless all five requests are in flight."""
    barrier = asyncio.Barrier(5)

    async def _gated(request: httpx.Request) -> Response:
        await barrier.wait()
        return Response(200, json=["value"])

    client = await _client()
    try:
        with respx.mock:
            for endpoint in ENDPOINTS:
                respx.get(_url(endpoint)).mock(side_effect=_gated)
            service = MetadataService(client)

            output = await asyncio.wait_for(service.get_metadata_page(MetadataCategory.ALL), timeout=5.0)
    finally:
        await client.close()

    assert output.page_info.total_count == 5


async def test_concurrency_never_exceeds_the_client_semaphore() -> None:
    in_flight = 0
    peak = 0

    async def _tracked(request: httpx.Request) -> Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return Response(200, json=["value"])

    client = await _client(max_concurrent=2)
    try:
        with respx.mock:
            for endpoint in ENDPOINTS:
                respx.get(_url(endpoint)).mock(side_effect=_tracked)

            await MetadataService(client).get_metadata_page(MetadataCategory.ALL)
    finally:
        await client.close()

    assert peak <= 2, f"observed {peak} concurrent requests against a semaphore of 2"


async def test_page_order_follows_metadata_order_not_arrival_order() -> None:
    """The last category answers first; the page must still start with keywords."""
    delays = dict(zip(ENDPOINTS, (0.05, 0.04, 0.03, 0.02, 0.01), strict=True))

    def _delayed(endpoint: str):  # type: ignore[no-untyped-def]
        async def _handler(request: httpx.Request) -> Response:
            await asyncio.sleep(delays[endpoint])
            return Response(200, json=[f"{endpoint}-a", f"{endpoint}-b"])

        return _handler

    client = await _client()
    try:
        with respx.mock:
            for endpoint in ENDPOINTS:
                respx.get(_url(endpoint)).mock(side_effect=_delayed(endpoint))

            output = await MetadataService(client).get_metadata_page(MetadataCategory.ALL, limit=3, offset=0)
    finally:
        await client.close()

    assert list(output.metadata) == list(CATEGORY_ORDER)
    assert output.metadata["keywords"] == ["keywords-a", "keywords-b"]
    assert output.metadata["publishers"] == ["acts-a"]
    assert output.metadata["statuses"] == []
    assert output.page_info.total_count == 10
    assert output.page_info.returned_count == 3


async def test_warm_cache_issues_no_requests() -> None:
    client = await _client()
    try:
        with respx.mock:
            routes = {
                endpoint: respx.get(_url(endpoint)).mock(return_value=Response(200, json=["value"]))
                for endpoint in ENDPOINTS
            }
            service = MetadataService(client)

            await service.get_metadata_page(MetadataCategory.ALL)
            counts_after_cold = {name: route.call_count for name, route in routes.items()}
            await service.get_metadata_page(MetadataCategory.ALL)
            counts_after_warm = {name: route.call_count for name, route in routes.items()}
    finally:
        await client.close()

    assert counts_after_cold == dict.fromkeys(ENDPOINTS, 1)
    assert counts_after_warm == counts_after_cold
