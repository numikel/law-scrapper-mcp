"""Coverage through the real tools: a fake module registers a coverage provider, the agent sees the result.

The service-level tests in `test_coverage_gap.py` prove the gap is computed; these prove each tool hands it
on (search and browse put it in the hints, the metadata tool lists the coverage), so unwiring a tool fails here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date

import httpx
import pytest
import respx
from mcp import Client

from fake_coverage_provider import FakeCoverageProvider, segment
from fake_source_modules import fake_module, loaded
from law_scrapper_mcp.server import build_app
from law_scrapper_mcp.source_modules.contract import ModuleHost, SourceModule
from mcp_helpers import parse_tool_result

pytestmark = pytest.mark.asyncio

SEARCH_URL = "https://api.sejm.gov.pl/eli/acts/search"
# A DU segment long past its newest announcement, with years that never include the ones asked below.
STALE_DU = segment("DU", newest=date(2020, 1, 1), newest_ref="2020/1", years=tuple(range(2000, 2031)))


def covering_module(provider: FakeCoverageProvider) -> SourceModule:
    @asynccontextmanager
    async def hook(host: ModuleHost) -> AsyncIterator[None]:
        host.coverage.register(provider)
        try:
            yield None
        finally:
            host.coverage.unregister(provider.key)

    return fake_module("alpha", [], start=hook)


@asynccontextmanager
async def session(provider: FakeCoverageProvider) -> AsyncIterator[Client]:
    async with Client(build_app([loaded(covering_module(provider))])) as client:
        yield client


def _hint_messages(payload: dict) -> list[str]:
    return [hint["message"] for hint in payload["hints"]]


@respx.mock
async def test_search_legal_acts_puts_the_gap_in_its_hints(search_results: dict) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=search_results))

    async with session(FakeCoverageProvider("DU", segments=(STALE_DU,))) as client:
        result = await client.call_tool("search_legal_acts", {"publisher": "DU"})

    payload = parse_tool_result(result)
    assert any(
        "Najnowsze ogłoszenie w Dziennik Ustaw pochodzi z 2020-01-01 (2020/1)" in m for m in _hint_messages(payload)
    )
    assert "coverage_gap" not in payload["data"], "the gap travels in the hints only"


@respx.mock
async def test_browse_acts_puts_the_year_gap_in_its_hints(search_results: dict) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=search_results))
    narrow = segment("DU", years=(2024, 2025))

    async with session(FakeCoverageProvider("DU", segments=(narrow,))) as client:
        result = await client.call_tool("browse_acts", {"publisher": "DU", "year": 1999})

    payload = parse_tool_result(result)
    assert any("nie ma rocznika 1999" in m for m in _hint_messages(payload))


@respx.mock
async def test_a_query_inside_the_coverage_gets_no_coverage_hint(search_results: dict) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=search_results))
    fresh = segment("DU", newest=date.today(), years=tuple(range(2000, 2031)))

    async with session(FakeCoverageProvider("DU", segments=(fresh,))) as client:
        result = await client.call_tool("search_legal_acts", {"publisher": "DU", "year": 2024})

    messages = _hint_messages(parse_tool_result(result))
    assert not any("Najnowsze ogłoszenie" in m or "nie ma rocznika" in m for m in messages)


@respx.mock
async def test_get_system_metadata_lists_the_registered_coverage() -> None:
    provider = FakeCoverageProvider("DU", segments=(STALE_DU,))

    async with session(provider) as client:
        result = await client.call_tool("get_system_metadata", {"category": "coverage"})

    payload = parse_tool_result(result)
    entries = payload["data"]["metadata"]["coverage"]
    assert [(entry["segment"], entry["origin"]) for entry in entries] == [("DU", "measured")]
    assert entries[0]["newest"] == "2020-01-01"
    assert len(provider.measure_calls) == 1
