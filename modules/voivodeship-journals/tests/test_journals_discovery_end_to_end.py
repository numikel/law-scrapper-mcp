"""Discovery through the real tool surface: the listing, a voivodeship name, an unknown publisher.

Tools are called through an in-memory MCP client over the real server with the module active. The
journal host of Pomerania and the Sejm are mocked by `respx`; any other request fails the test, and the
Sejm's search endpoint is a tripwire that must stay silent whenever a journal is addressed.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import respx
from journal_support import FIXTURES, PM, Session, journal_session, load_json
from mcp import Client

from law_scrapper_mcp.config import Settings
from law_scrapper_mcp.server import build_app
from law_scrapper_mcp.source_modules.discovery import discover
from law_scrapper_mcp_voivodeship_journals.catalog import JOURNALS, WITHHELD

pytestmark = pytest.mark.asyncio

SEJM = "https://api.sejm.gov.pl/eli"
GUIDANCE = "Popraw parametr wywołania i spróbuj ponownie."


@dataclass
class World:
    """The mocked hosts: one journal, and the Sejm with a tripwire on its search endpoint."""

    router: respx.MockRouter
    pm_search: respx.Route
    sejm_acts: respx.Route
    sejm_search: respx.Route


@pytest.fixture
def world() -> Iterator[World]:
    publishers = json.loads((FIXTURES.parent / "publishers.json").read_text(encoding="utf-8"))
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield World(
            router=router,
            pm_search=router.get(f"{PM}/search").mock(
                return_value=httpx.Response(200, json=load_json("search_page_camel.json"))
            ),
            sejm_acts=router.get(f"{SEJM}/acts").mock(return_value=httpx.Response(200, json=publishers)),
            sejm_search=router.get(f"{SEJM}/acts/search").mock(return_value=httpx.Response(500)),
        )


@asynccontextmanager
async def core_only_session() -> AsyncIterator[Session]:
    """The real server with every module switched off."""
    discovery = discover(Settings(source_modules=["-*"]))
    assert len(discovery.active) == 0
    async with Client(build_app(discovery.active, inactive=discovery.inactive)) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        yield Session(client=client, tools=tools)


# --- the listing -------------------------------------------------------------------------------


async def test_the_listing_names_all_sixteen_journals_after_du_and_mp(world: World) -> None:
    async with journal_session() as session:
        data = await session.call("get_system_metadata", {"category": "publishers", "limit": 100})

    entries = data["metadata"]["publishers"]
    assert [entry["code"] for entry in entries[:2]] == ["DU", "MP"]
    journals = {entry["code"]: entry for entry in entries[2:]}
    assert sorted(journals) == sorted(journal.code for journal in JOURNALS)
    assert data["page_info"]["total_count"] == 18
    assert data["failed_categories"] == []
    assert {code for code, entry in journals.items() if not entry["available"]} == {j.code for j in WITHHELD}
    for journal in WITHHELD:
        assert journals[journal.code]["withheldReason"] == journal.withheld
    assert "withheldReason" not in journals["POL_WOJ_PM"]
    assert journals["POL_WOJ_PM"]["voivodeship"] == "pomorskie"
    assert (world.sejm_acts.call_count, world.pm_search.call_count) == (1, 0), "the listing costs one Sejm request"


async def test_the_listing_comes_with_the_hint_that_explains_names(world: World) -> None:
    async with journal_session() as session:
        result = await session.client.call_tool("get_system_metadata", {"category": "publishers"})

    assert result.structured_content is not None
    messages = " ".join(hint["message"] for hint in result.structured_content["hints"])
    assert "kodem albo nazwą z listy" in messages and "w ELI zawsze używaj kodu" in messages


# --- a name in the publisher parameter ---------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        pytest.param("search_legal_acts", {"publisher": "Pomorskie", "year": 2025, "limit": 5}, id="search"),
        pytest.param("browse_acts", {"publisher": "pomorskie", "year": 2025, "limit": 5}, id="browse"),
        pytest.param(
            "track_legal_changes",
            {"publisher": "POMORSKIE", "date_from": "2025-12-01", "date_to": "2025-12-31", "limit": 5},
            id="track changes",
        ),
    ],
)
async def test_a_name_reaches_that_journal_and_never_the_sejm(
    world: World, tool: str, arguments: dict[str, Any]
) -> None:
    async with journal_session() as session:
        data = await session.call(tool, arguments)

    assert world.pm_search.call_count == 1
    assert world.sejm_search.call_count == 0
    if tool != "track_legal_changes":
        assert all(result["eli"].startswith("POL_WOJ_PM/2025/") for result in data["results"])
    else:
        assert data["publisher"] == arguments["publisher"], (
            "the query echoes the name as typed; the acts carry the code"
        )
        assert all(change["eli"].startswith("POL_WOJ_PM/") for change in data["changes"])


async def test_a_withheld_journal_addressed_by_name_fails_with_its_reason_and_costs_nothing(world: World) -> None:
    async with journal_session() as session:
        text = await session.fail("search_legal_acts", {"publisher": "Łódzkie", "year": 2025})

    assert "POL_WOJ_LD" in text and "wyłączony" in text
    assert len(world.router.calls) == 0


# --- an unknown publisher ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments", "echoed"),
    [
        pytest.param("search_legal_acts", {"publisher": "xyz", "year": 2025}, "xyz", id="search, no prefix"),
        pytest.param("search_legal_acts", {"publisher": "POL_WOJ_XX", "year": 2025}, "POL_WOJ_XX", id="search, prefix"),
        pytest.param("browse_acts", {"publisher": "pomorskei", "year": 2025}, "pomorskei", id="browse, typo"),
        pytest.param("get_act_details", {"eli": "xyz/2025/1"}, "xyz", id="ELI of nobody"),
    ],
)
async def test_an_unknown_publisher_gets_the_valid_codes_with_their_names(
    world: World, tool: str, arguments: dict[str, Any], echoed: str
) -> None:
    async with journal_session() as session:
        text = await session.fail(tool, arguments)

    assert f"Nieznany wydawca: {echoed}." in text
    assert "Zamiast kodu można podać nazwę" in text
    assert "DU, MP" in text and "POL_WOJ_PM (pomorskie)" in text and "POL_WOJ_ZP (zachodniopomorskie)" in text
    assert not any(journal.code in text for journal in WITHHELD), "a withheld journal is not offered as valid"
    assert text.endswith(GUIDANCE)
    assert len(world.router.calls) == 0, "a refusal costs no request"


async def test_a_long_unknown_value_is_cut_in_the_echo_and_the_list_survives(world: World) -> None:
    async with journal_session() as session:
        text = await session.fail("search_legal_acts", {"publisher": "x" * 300, "year": 2025})

    assert "x" * 41 not in text
    assert "POL_WOJ_ZP (zachodniopomorskie)." in text, "the last entry of the list is still there"
    assert text.endswith(GUIDANCE)


# --- a name inside an ELI ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        pytest.param("get_act_details", {"eli": "pomorskie/2025/5051"}, id="details"),
        pytest.param(
            "get_act_details", {"eli": "Pomorskie/2025/5051", "load_content": True}, id="details with content"
        ),
        pytest.param("analyze_act_relationships", {"eli": "pomorskie/2025/5049"}, id="relations"),
        # Both sides carry the name: the two fetches run concurrently, so a code on one side would race the refusal.
        pytest.param("compare_acts", {"eli_a": "pomorskie/2025/5051", "eli_b": "pomorskie/2025/5049"}, id="compare"),
    ],
)
async def test_a_name_inside_an_eli_is_refused_with_the_code_to_use(
    world: World, tool: str, arguments: dict[str, Any]
) -> None:
    async with journal_session() as session:
        text = await session.fail(tool, arguments)

    assert "W ELI podaj kod dziennika" in text
    assert "pomorskie = POL_WOJ_PM" in text
    assert text.endswith(GUIDANCE)
    assert len(world.router.calls) == 0


# --- without the module ------------------------------------------------------------------------


async def test_with_the_module_off_the_list_is_the_sejms_and_an_unknown_value_still_goes_to_the_sejm(
    world: World,
) -> None:
    world.sejm_search.mock(
        return_value=httpx.Response(200, json=json.loads((FIXTURES.parent / "search_results.json").read_text("utf-8")))
    )

    async with core_only_session() as session:
        listing = await session.client.call_tool("get_system_metadata", {"category": "publishers"})
        found = await session.client.call_tool("search_legal_acts", {"publisher": "xyz", "year": 2024})

    assert listing.structured_content is not None and found.is_error is False
    assert [entry["code"] for entry in listing.structured_content["data"]["metadata"]["publishers"]] == ["DU", "MP"]
    assert "w ELI zawsze używaj kodu" not in " ".join(hint["message"] for hint in listing.structured_content["hints"])
    assert world.sejm_search.call_count == 1
    assert world.sejm_search.calls.last.request.url.params["publisher"] == "xyz"
    assert world.pm_search.call_count == 0
