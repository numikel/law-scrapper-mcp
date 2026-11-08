"""Coverage of the voivodeship journals: the provider on the recordings and the hints an agent reads.

Every host is mocked by `respx` and the network guard of `conftest.py` stays on. Recordings used as they are:
the 2026 year pages and the two last-position act details of the fourth session (`PROVENANCE.md`), the two
publisher lists (`publishers_camel.json` is PM, `publishers_pascal.json` is KP) and the Sejm recordings.
Shapes with no recording of their own are derived in the test from a recording of the same casing and
labelled as derived: the publisher list of a host other than PM and KP, and the detail of the newest act
of a host other than SK and WP (the act detail of 2025/5051, or 2025/6588 for a Pascal host, stands in).
"""

from __future__ import annotations

import builtins
import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from journal_support import FIXTURES, Started, journal_session, load_json

from law_scrapper_mcp.services.coverage import FALLBACK_NOTE, CoverageRegistry, act_coverage_gap
from law_scrapper_mcp.source_modules.contract import ModuleHost
from law_scrapper_mcp_voivodeship_journals import start
from law_scrapper_mcp_voivodeship_journals.catalog import BY_CODE, SERVED, WITHHELD
from law_scrapper_mcp_voivodeship_journals.coverage import NEWEST_RECORDED

pytestmark = pytest.mark.asyncio

SERVED_CODES = tuple(journal.code for journal in SERVED)
SEJM_RECORDINGS = FIXTURES.parent / "sejm"
SEJM_ACTS = "https://api.sejm.gov.pl/eli/acts"
SEJM_SEARCH = "https://api.sejm.gov.pl/eli/acts/search"
# Two days after the recorded newest acts of 2026-11-05/06: inside the 30-day window of every journal.
RECORDING_DAY = date(2026, 11, 9)


class Today:
    """The registry's clock, moved by hand."""

    def __init__(self, day: date = RECORDING_DAY) -> None:
        self.day = day

    def __call__(self) -> date:
        return self.day


@pytest.fixture
def today(monkeypatch: pytest.MonkeyPatch) -> Today:
    """Make the app's coverage registry read this clock instead of the wall clock."""
    from law_scrapper_mcp import server

    clock = Today()
    monkeypatch.setattr(server, "CoverageRegistry", lambda: CoverageRegistry(today=clock))
    return clock


# --- the recorded hosts ---------------------------------------------------------------------------


def year_page(code: str) -> dict[str, Any]:
    """The recorded `search?year=2026&limit=1` page of a journal."""
    page: dict[str, Any] = load_json(f"year_page_2026_{code[-2:].lower()}.json")
    return page


def total_of(page: dict[str, Any]) -> int:
    total = page["TotalCount"] if "Items" in page else page["totalCount"]
    assert isinstance(total, int)
    return total


def listing_for(code: str, page: dict[str, Any]) -> list[dict[str, Any]]:
    """Derived: the recorded publisher list of the same casing (PM or KP) under this journal's code."""
    entry: dict[str, Any] = load_json("publishers_pascal.json" if "Items" in page else "publishers_camel.json")[0]
    return [{**entry, ("Code" if "Code" in entry else "code"): code}]


def detail_for(code: str, page: dict[str, Any]) -> dict[str, Any]:
    """The recorded last-position detail for SK and WP; for the rest a stand-in of the same casing."""
    if code in ("POL_WOJ_SK", "POL_WOJ_WP"):
        recorded: dict[str, Any] = load_json(f"act_detail_last_position_{code[-2:].lower()}.json")
        return recorded
    stand_in: dict[str, Any] = load_json("act_detail_pascal.json" if "Items" in page else "act_detail_camel.json")
    return stand_in


def mock_journal(
    router: respx.MockRouter,
    code: str,
    *,
    listing: httpx.Response | None = None,
    detail: httpx.Response | None = None,
) -> dict[str, respx.Route]:
    """Mock the three measurement requests of one journal host; each default is built from a recording."""
    base = BY_CODE[code].base_url
    page = year_page(code)
    total = total_of(page)
    return {
        "acts": router.get(f"{base}/acts").mock(
            return_value=listing if listing is not None else httpx.Response(200, json=listing_for(code, page))
        ),
        "search": router.get(f"{base}/search").mock(return_value=httpx.Response(200, json=page)),
        "detail": router.get(f"{base}/acts/{code}/2026/{total}").mock(
            return_value=detail if detail is not None else httpx.Response(200, json=detail_for(code, page))
        ),
    }


def mock_sejm(router: respx.MockRouter) -> None:
    router.get(SEJM_ACTS).mock(
        return_value=httpx.Response(
            200, json=json.loads((SEJM_RECORDINGS / "publishers_live.json").read_text(encoding="utf-8"))
        )
    )
    for code, name in (("DU", "du"), ("MP", "mp")):
        body = json.loads((SEJM_RECORDINGS / f"search_2026_limit1_{name}.json").read_text(encoding="utf-8"))
        router.get(SEJM_SEARCH, params={"publisher": code, "year": "2026", "limit": "1"}).mock(
            return_value=httpx.Response(200, json=body)
        )


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as mocked:
        yield mocked


def hint_messages(result: Any) -> list[str]:
    assert result.is_error is False, result.content
    return [hint["message"] for hint in result.structured_content["hints"]]


# --- what the agent reads -------------------------------------------------------------------------


async def test_a_year_pomorskie_never_published_gets_the_year_gap_hint(router: respx.MockRouter, today: Today) -> None:
    mock_journal(router, "POL_WOJ_PM")

    async with journal_session() as session:
        result = await session.client.call_tool("search_legal_acts", {"publisher": "POL_WOJ_PM", "year": 2007})

    hints = [m for m in hint_messages(result) if "nie ma rocznika" in m]
    assert len(hints) == 1
    assert "Dz. Urz. Woj. Pomorskiego nie ma rocznika 2007" in hints[0]
    assert "2001–2003, 2005, 2009–2026" in hints[0]
    assert "pomiar z 2026-11-09" in hints[0]


async def test_wielkopolskie_is_fresh_because_the_newest_is_read_from_the_last_position_not_the_first_item(
    router: respx.MockRouter, today: Today
) -> None:
    """Q-11: WP lists a 2026-07-15 act first, yet published through 2026-11-06. A first-item reading would hint."""
    first_item = year_page("POL_WOJ_WP")["items"][0]
    assert first_item["announcementDate"].startswith("2026-07-15"), "the premise of the regression"
    assert (today.day - date(2026, 7, 15)).days > 30, "a first-item reading would be stale on this day"
    routes = mock_journal(router, "POL_WOJ_WP")

    async with journal_session() as session:
        fresh = await session.client.call_tool("search_legal_acts", {"publisher": "POL_WOJ_WP", "year": 2026})
        # Same measurement (remembered for a day), later day: only the clock moves.
        today.day = date(2026, 12, 20)
        stale = await session.client.call_tool("search_legal_acts", {"publisher": "POL_WOJ_WP", "year": 2026})

    assert [m for m in hint_messages(fresh) if "Najnowsze ogłoszenie" in m or "nie ma rocznika" in m] == []
    late = [m for m in hint_messages(stale) if "Najnowsze ogłoszenie" in m]
    assert [route.call_count for route in routes.values()] == [1, 2, 1], (
        "one measurement plus the agent's search (its repeat is cached)"
    )
    assert len(late) == 1
    assert FALLBACK_NOTE not in late[0]
    assert "pochodzi z 2026-11-06 (2026/8206)" in late[0]


async def test_the_coverage_category_lists_du_mp_and_the_twelve_served_journals_at_three_requests_a_host(
    router: respx.MockRouter, today: Today
) -> None:
    mock_sejm(router)
    routes = {code: mock_journal(router, code) for code in SERVED_CODES}

    async with journal_session() as session:
        entries = (await session.call("get_system_metadata", {"category": "coverage"}))["metadata"]["coverage"]
        sejm_requests = sum(1 for call in router.calls if call.request.url.host == "api.sejm.gov.pl")

    by_segment = {entry["segment"]: entry for entry in entries}
    assert set(by_segment) == {"DU", "MP", *SERVED_CODES}
    assert len(entries) == 14
    assert not {journal.code for journal in WITHHELD} & set(by_segment), "a withheld journal has no provider"
    assert {entry["origin"] for entry in entries} == {"measured"}
    assert {entry["source"] for entry in entries if entry["segment"] in SERVED_CODES} == {"voivodeship-journals"}
    assert {entry["source"] for entry in entries if entry["segment"] in ("DU", "MP")} == {"sejm"}
    assert sejm_requests == 3
    for code, host_routes in routes.items():
        assert [route.call_count for route in host_routes.values()] == [1, 1, 1], code
    # Both casings reach the count: PM answers `deedsCount`, KP `count`.
    assert by_segment["POL_WOJ_PM"]["count"] == 97_904
    assert by_segment["POL_WOJ_KP"]["count"] == 100_924
    assert by_segment["POL_WOJ_SK"]["newest_ref"] == "2026/3577"
    assert by_segment["POL_WOJ_PM"]["label"] == BY_CODE["POL_WOJ_PM"].short_name


# --- the provider on its own ----------------------------------------------------------------------


async def test_a_204_on_the_newest_detail_leaves_the_date_unknown_but_keeps_the_years(
    started: Started, router: respx.MockRouter
) -> None:
    mock_journal(router, "POL_WOJ_PM", detail=httpx.Response(204))

    coverage = await started.host.coverage.get("POL_WOJ_PM")

    assert coverage is not None and coverage.origin == "measured"
    segment = coverage.segments[0]
    assert (segment.newest, segment.newest_ref) == (None, None)
    assert segment.years is not None and 2007 not in segment.years and segment.years[-1] == 2026
    kwargs: dict[str, Any] = {
        "pub_date_from": None,
        "pub_date_to": None,
        "today": coverage.measured_on,
        "origin": coverage.origin,
    }
    missing = act_coverage_gap(segment, year=2007, **kwargs)
    assert missing is not None and "nie ma rocznika 2007" in missing
    assert act_coverage_gap(segment, year=2026, **kwargs) is None, "no date, no freshness gap"


async def test_an_empty_newest_year_asks_for_the_year_before_it(started: Started, router: respx.MockRouter) -> None:
    """Derived bodies: the recorded PM year page with its count and item emptied, and a count for 2025."""
    base = BY_CODE["POL_WOJ_PM"].base_url
    page = year_page("POL_WOJ_PM")
    router.get(f"{base}/acts").mock(return_value=httpx.Response(200, json=listing_for("POL_WOJ_PM", page)))
    router.get(f"{base}/search", params={"year": "2026", "limit": "1"}).mock(
        return_value=httpx.Response(200, json={**page, "items": [], "count": 0, "totalCount": 0})
    )
    router.get(f"{base}/search", params={"year": "2025", "limit": "1"}).mock(
        return_value=httpx.Response(200, json={**page, "totalCount": 7})
    )
    detail = router.get(f"{base}/acts/POL_WOJ_PM/2025/7").mock(
        return_value=httpx.Response(200, json=detail_for("POL_WOJ_PM", page))
    )

    coverage = await started.host.coverage.get("POL_WOJ_PM")

    assert coverage is not None and coverage.origin == "measured"
    assert coverage.segments[0].newest_ref == "2025/7"
    assert detail.call_count == 1
    assert router.calls.call_count == 4, "listing, two searches, one detail"


async def test_a_failing_host_is_answered_with_the_recorded_newest_and_no_years(
    started: Started, router: respx.MockRouter
) -> None:
    base = BY_CODE["POL_WOJ_PM"].base_url
    router.get(f"{base}/acts").mock(return_value=httpx.Response(404))

    coverage = await started.host.coverage.get("POL_WOJ_PM")

    assert coverage is not None and coverage.origin == "fallback"
    segment = coverage.segments[0]
    assert (segment.newest, segment.newest_ref, segment.years) == (date(2026, 11, 5), "2026/4305", None)


async def test_the_start_hook_registers_a_provider_per_served_journal_and_removes_them_on_exit() -> None:
    from journal_support import CALLER

    from law_scrapper_mcp.client.cache import TTLCache
    from law_scrapper_mcp.client.egress import EgressDefaults, EgressHub
    from law_scrapper_mcp.config import Settings
    from law_scrapper_mcp.services.act_sources import ActSourceRegistry

    hub = EgressHub(TTLCache(max_entries=10), EgressDefaults(user_agent=Settings().user_agent))
    host = ModuleHost(egress=hub, caller=CALLER, settings=Settings(), act_sources=ActSourceRegistry())
    try:
        async with start(host):
            assert host.coverage.keys() == tuple(sorted(SERVED_CODES))
        assert host.coverage.keys() == ()
    finally:
        await hub.close()


# --- the fallback constants -----------------------------------------------------------------------


async def test_every_served_journal_has_a_recorded_newest_and_no_withheld_one_does() -> None:
    assert set(NEWEST_RECORDED) == set(SERVED_CODES)


async def test_the_recorded_newest_equals_the_recordings() -> None:
    """Ten hosts: first item of the 2026 page; SK and WP: the detail at the last position."""
    for code in SERVED_CODES:
        page = year_page(code)
        if code in ("POL_WOJ_SK", "POL_WOJ_WP"):
            detail = detail_for(code, page)
            published, pos = detail["promulgation"], detail["pos"]
        else:
            items = page["Items"] if "Items" in page else page["items"]
            item = {key[:1].lower() + key[1:]: value for key, value in items[0].items()}
            published, pos = item["announcementDate"], item["pos"]
        assert NEWEST_RECORDED[code] == (date.fromisoformat(published[:10]), f"2026/{pos}"), code


async def test_the_fallback_reads_no_file(monkeypatch: pytest.MonkeyPatch) -> None:
    from law_scrapper_mcp.client.cache import TTLCache
    from law_scrapper_mcp.client.egress import EgressClient, EgressHost
    from law_scrapper_mcp_voivodeship_journals.coverage import JournalCoverageProvider

    journal = BY_CODE["POL_WOJ_SK"]
    client = EgressClient(EgressHost(name=journal.code, base_url=journal.base_url), TTLCache(max_entries=1))
    provider = JournalCoverageProvider(journal, client)

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the fallback must not read a file")

    with monkeypatch.context() as patched:
        patched.setattr(builtins, "open", refuse)
        patched.setattr(Path, "read_text", refuse)
        patched.setattr(Path, "open", refuse)
        coverage = provider.fallback()

    segment = coverage.segments[0]
    assert coverage.origin == "fallback" and coverage.measured_on == date(2026, 11, 8)
    assert (segment.key, segment.newest, segment.newest_ref, segment.years) == (
        "POL_WOJ_SK",
        date(2026, 11, 6),
        "2026/3577",
        None,
    )
