"""Publisher discovery through the real tools: a fake module describes its publishers, nothing else is real.

The end-to-end proof with the actual voivodeship module is in
`modules/voivodeship-journals/tests/test_journals_discovery_end_to_end.py`; this file pins the core's
side with a source that is nothing but a recording fake, so it cannot pass or fail for a module's reasons.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
import respx
from mcp import Client

from fake_act_source import FakeActSource
from fake_source_modules import fake_module, loaded
from law_scrapper_mcp.config import settings
from law_scrapper_mcp.server import build_app
from law_scrapper_mcp.services.act_sources import PublisherInfo
from law_scrapper_mcp.source_modules.contract import ModuleHost, SourceModule
from mcp_helpers import parse_tool_result

pytestmark = pytest.mark.asyncio

SEJM = "https://api.sejm.gov.pl/eli"
INFOS = [
    PublisherInfo(
        code="ZZ_PM",
        label="pomorskie",
        available=True,
        item={"code": "ZZ_PM", "voivodeship": "pomorskie", "available": True},
    ),
    PublisherInfo(
        code="ZZ_MZ",
        label="mazowieckie",
        available=False,
        item={"code": "ZZ_MZ", "voivodeship": "mazowieckie", "available": False, "withheldReason": "test"},
    ),
]


def describing_module(source: FakeActSource) -> SourceModule:
    @asynccontextmanager
    async def hook(host: ModuleHost) -> AsyncIterator[None]:
        host.act_sources.register(source, publishers=[info.code for info in INFOS], prefixes=["ZZ_"], info=INFOS)
        try:
            yield None
        finally:
            host.act_sources.unregister(source)

    return fake_module("alpha", [], start=hook)


@asynccontextmanager
async def session(source: FakeActSource) -> AsyncIterator[Client]:
    async with Client(build_app([loaded(describing_module(source))])) as client:
        yield client


@respx.mock
async def test_get_system_metadata_lists_the_described_publishers_after_the_sejms(publishers_data: list) -> None:
    respx.get(f"{SEJM}/acts").mock(return_value=httpx.Response(200, json=publishers_data))

    async with session(FakeActSource()) as client:
        result = await client.call_tool("get_system_metadata", {"category": "publishers"})

    payload = parse_tool_result(result)
    assert [entry["code"] for entry in payload["data"]["metadata"]["publishers"]] == ["DU", "MP", "ZZ_MZ", "ZZ_PM"]
    assert payload["data"]["metadata"]["publishers"][2]["withheldReason"] == "test"
    assert payload["data"]["failed_categories"] == []
    assert any("w ELI zawsze używaj kodu" in hint["message"] for hint in payload["hints"])


@respx.mock
async def test_a_name_in_the_publisher_parameter_reaches_the_source_and_never_the_sejm() -> None:
    sejm = respx.route(host="api.sejm.gov.pl").mock(return_value=httpx.Response(500))
    source = FakeActSource(total=3)

    async with session(source) as client:
        result = await client.call_tool("search_legal_acts", {"publisher": "Pomorskie", "year": 2025, "limit": 2})

    payload = parse_tool_result(result)
    assert payload["data"]["total_count"] == 3
    assert [call[0] for call in source.calls] == ["search"]
    assert sejm.call_count == 0


@respx.mock
async def test_an_unknown_publisher_is_refused_with_the_valid_ones_and_never_reaches_the_sejm() -> None:
    sejm = respx.route(host="api.sejm.gov.pl").mock(return_value=httpx.Response(500))
    source = FakeActSource()

    async with session(source) as client:
        result = await client.call_tool("search_legal_acts", {"publisher": "xyz", "year": 2025})

    assert result.is_error is True
    text = result.content[0].text  # type: ignore[union-attr]
    assert "Nieznany wydawca: xyz." in text
    assert "ZZ_PM (pomorskie)" in text and "DU, MP" in text
    assert "ZZ_MZ" not in text, "an unavailable publisher is not offered"
    assert text.endswith("Popraw parametr wywołania i spróbuj ponownie.")
    assert source.calls == []
    assert sejm.call_count == 0


@respx.mock
async def test_the_refusal_reaches_the_agent_whole_even_for_a_long_input() -> None:
    respx.route(host="api.sejm.gov.pl").mock(return_value=httpx.Response(500))

    async with session(FakeActSource()) as client:
        result = await client.call_tool("search_legal_acts", {"publisher": "x" * 300, "year": 2025})

    text = result.content[0].text  # type: ignore[union-attr]
    assert "Poprawne: DU, MP, ZZ_PM (pomorskie)" in text, "the list survives the error-length cap"
    assert len(text) <= settings.error_message_max_chars + len(" Popraw parametr wywołania i spróbuj ponownie.")


@respx.mock
async def test_du_and_mp_in_any_letter_case_still_go_to_the_sejm_while_a_source_is_active(
    search_results: dict,
) -> None:
    route = respx.get(f"{SEJM}/acts/search").mock(return_value=httpx.Response(200, json=search_results))
    source = FakeActSource()

    async with session(source) as client:
        result = await client.call_tool("search_legal_acts", {"publisher": "du", "year": 2024})

    parse_tool_result(result)
    assert route.call_count == 1
    assert source.calls == []


@respx.mock
async def test_without_any_module_an_unknown_publisher_goes_to_the_sejm_as_in_4_4_2(
    search_results: dict, publishers_data: list
) -> None:
    route = respx.get(f"{SEJM}/acts/search").mock(return_value=httpx.Response(200, json=search_results))
    respx.get(f"{SEJM}/acts").mock(return_value=httpx.Response(200, json=publishers_data))

    async with Client(build_app()) as client:
        search = await client.call_tool("search_legal_acts", {"publisher": "xyz", "year": 2024})
        listing = await client.call_tool("get_system_metadata", {"category": "publishers"})

    parse_tool_result(search)
    assert route.call_count == 1
    assert route.calls.last.request.url.params["publisher"] == "xyz"
    assert [entry["code"] for entry in parse_tool_result(listing)["data"]["metadata"]["publishers"]] == ["DU", "MP"]
    assert not any("w ELI zawsze używaj kodu" in hint["message"] for hint in parse_tool_result(listing)["hints"])
