"""The act coverage-gap rule, its Polish texts, and how the search path carries the message to the hints."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
import respx
from httpx import Response
from mcp import Client

from fake_act_source import FakeActSource
from fake_coverage_provider import MEASURED_ON, Clock, FakeCoverageProvider, segment
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.models.tool_outputs import SearchOutput
from law_scrapper_mcp.server import app
from law_scrapper_mcp.services.act_sources import ActSourceRegistry, PublisherInfo
from law_scrapper_mcp.services.coverage import (
    FALLBACK_NOTE,
    STALE_AFTER_DAYS,
    CoverageRegistry,
    CoverageSegment,
    act_coverage_gap,
    format_years,
)
from law_scrapper_mcp.services.pagination import item_page_info
from law_scrapper_mcp.services.response_enrichment import search_hints
from law_scrapper_mcp.services.result_store import ResultStore
from law_scrapper_mcp.services.search_service import SearchService

TODAY = date(2026, 11, 8)
SEARCH_URL = "https://api.sejm.gov.pl/eli/acts/search"
# Publication years of the Pomeranian journal with the 2007 hole the rule is pinned on.
PM_YEARS = (*range(2001, 2007), *range(2008, 2027))


def _gap(seg: CoverageSegment, **overrides: Any) -> str | None:
    arguments: dict[str, Any] = {
        "year": None,
        "pub_date_from": None,
        "pub_date_to": None,
        "today": TODAY,
        "origin": "measured",
        "measured_on": MEASURED_ON,
    }
    arguments.update(overrides)
    return act_coverage_gap(seg, **arguments)


# --- format_years ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("years", "expected"),
    [
        ((2024,), "2024"),
        ((2001, 2002, 2003, 2005, 2009, 2010, 2011), "2001–2003, 2005, 2009–2011"),
        ((2026, 2024, 2025, 2024), "2024–2026"),
        ((2001, 2003), "2001, 2003"),
        ((), "brak"),
    ],
)
def test_years_are_rendered_as_compact_ranges(years: tuple[int, ...], expected: str) -> None:
    assert format_years(years) == expected


# --- the year gap ------------------------------------------------------------------------------


def test_a_year_in_the_hole_of_the_journal_is_a_gap_and_the_text_lists_the_ranges() -> None:
    pm = segment("POL_WOJ_PM", label="Dziennik Urzędowy Województwa Pomorskiego", years=PM_YEARS)

    assert _gap(pm, year=2007) == (
        "Źródło „Dziennik Urzędowy Województwa Pomorskiego” nie ma rocznika 2007 "
        "(dostępne: 2001–2006, 2008–2026; pomiar z 2026-11-08)."
    )


def test_a_year_the_source_has_is_not_a_gap() -> None:
    pm = segment("POL_WOJ_PM", years=PM_YEARS)

    assert _gap(pm, year=2008) is None


def test_no_year_asked_means_no_year_gap() -> None:
    assert _gap(segment(years=(2020,)), year=None) is None


def test_a_fallback_without_years_never_reports_a_year_gap() -> None:
    assert _gap(segment(years=None), year=1999, origin="fallback") is None


def test_known_but_empty_years_make_every_year_a_gap() -> None:
    message = _gap(segment(years=()), year=2024)

    assert message is not None
    assert "(dostępne: brak;" in message


def test_the_measurement_date_is_left_out_when_the_caller_does_not_know_it() -> None:
    message = _gap(segment(years=(2020,)), year=2024, measured_on=None)

    assert message is not None
    assert message.endswith("(dostępne: 2020).")
    assert "pomiar" not in message


# --- the freshness gap -------------------------------------------------------------------------


def test_exactly_thirty_days_past_the_newest_is_not_a_gap() -> None:
    newest = TODAY - timedelta(days=STALE_AFTER_DAYS)

    assert _gap(segment(newest=newest)) is None


def test_thirty_one_days_past_the_newest_is_a_gap_with_the_reference() -> None:
    newest = TODAY - timedelta(days=STALE_AFTER_DAYS + 1)

    assert _gap(segment(newest=newest, newest_ref="2026/5811")) == (
        "Najnowsze ogłoszenie w źródle „Dziennik Ustaw” pochodzi z 2026-10-08 (2026/5811); "
        "zapytanie sięga dalej (pomiar z 2026-11-08)."
    )


def test_a_missing_reference_drops_the_brackets() -> None:
    message = _gap(segment(newest=date(2026, 1, 1), newest_ref=None))

    assert message is not None
    assert "pochodzi z 2026-01-01;" in message


def test_an_unknown_newest_date_is_never_a_freshness_gap() -> None:
    assert _gap(segment(newest=None, newest_ref=None)) is None


def test_unknown_newest_with_known_years_still_reports_a_missing_year_and_never_freshness() -> None:
    """A journal whose act detail answered 204 has years but no newest date; the year gap must survive."""
    seg = segment(newest=None, newest_ref=None, years=(2024, 2025, 2026))

    missing = _gap(seg, year=2019)
    assert missing is not None
    assert "nie ma rocznika 2019" in missing
    assert "Najnowsze ogłoszenie" not in missing
    # A year the segment has, even far in the future of an unknown newest date, is no freshness gap.
    assert _gap(seg, year=2026) is None
    assert _gap(seg, pub_date_to="2030-01-01") is None


def test_the_end_of_the_window_decides_not_today() -> None:
    seg = segment(newest=date(2026, 6, 1))

    assert _gap(seg, pub_date_to="2026-06-30") is None
    assert _gap(seg, pub_date_to="2026-07-02") is not None


def test_pub_date_to_wins_over_the_year() -> None:
    seg = segment(newest=date(2026, 6, 1), years=(2025, 2026))

    assert _gap(seg, year=2026, pub_date_to="2026-06-15") is None


def test_the_end_of_a_past_year_is_the_bound_not_today() -> None:
    seg = segment(newest=date(2026, 11, 1), years=(2024, 2025, 2026))

    assert _gap(seg, year=2025) is None


def test_the_current_year_is_capped_at_today() -> None:
    seg = segment(newest=date(2026, 9, 1), years=(2025, 2026))

    assert _gap(seg, year=2026) is not None  # 8 Nov is more than 30 days past 1 Sep, 31 Dec is not the bound


def test_an_unparseable_window_end_is_unknown_not_a_gap() -> None:
    assert _gap(segment(newest=date(2020, 1, 1)), pub_date_to="jutro") is None


def test_the_lower_bound_of_the_window_is_not_read() -> None:
    seg = segment(newest=TODAY)

    assert _gap(seg, pub_date_from="2020-01-01") == _gap(seg, pub_date_from=None)


# --- both gaps, fallback ---------------------------------------------------------------------


def test_a_missing_year_is_reported_alone_even_when_the_source_is_stale_too() -> None:
    seg = segment(newest=date(2020, 1, 1), years=(2019, 2020))

    message = _gap(seg, year=2025)

    assert message is not None
    assert "nie ma rocznika 2025" in message
    assert "Najnowsze ogłoszenie" not in message


def test_the_fallback_note_is_appended_to_either_message() -> None:
    year_gap = _gap(segment(years=(2020,)), year=2025, origin="fallback")
    freshness_gap = _gap(segment(newest=date(2020, 1, 1)), origin="fallback")

    assert year_gap is not None and year_gap.endswith(FALLBACK_NOTE)
    assert freshness_gap is not None and freshness_gap.endswith(FALLBACK_NOTE)
    assert FALLBACK_NOTE == "Wartość zapasowa — bieżący pomiar się nie udał."
    measured = _gap(segment(newest=date(2020, 1, 1)))
    assert measured is not None and FALLBACK_NOTE not in measured


# --- search_hints ------------------------------------------------------------------------------


def _hints(**overrides: Any) -> list[Any]:
    arguments: dict[str, Any] = {
        "total_count": 0,
        "has_results": False,
        "tool_name": "search_legal_acts",
        "next_call_params": {"publisher": "DU"},
        "filter_max_records": 1000,
    }
    arguments.update(overrides)
    return search_hints(arguments.pop("total_count"), arguments.pop("has_results"), **arguments)


def test_the_gap_becomes_a_plain_hint_ahead_of_the_empty_result_advice() -> None:
    hints = _hints(coverage_gap="Luka.")

    messages = [hint.message for hint in hints]
    assert "Luka." in messages
    assert messages.index("Luka.") < messages.index(
        "Spróbuj poszerzyć kryteria: usuń filtry dat, zmień typ dokumentu lub rok."
    )
    gap_hint = hints[messages.index("Luka.")]
    assert gap_hint.tool is None and gap_hint.parameters is None


def test_no_gap_leaves_the_hints_untouched() -> None:
    assert _hints() == _hints(coverage_gap=None)
    assert _hints() == _hints(coverage_gap="")


# --- SearchOutput: the message is private ------------------------------------------------------


def _output() -> SearchOutput:
    return SearchOutput(
        results=[],
        total_count=0,
        query_summary="",
        returned_count=0,
        page_info=item_page_info(limit=20, offset=0, returned=0, total=0),
    )


def test_the_gap_message_is_readable_but_never_serialised() -> None:
    output = _output().with_coverage_gap("Luka.")

    assert output.coverage_gap == "Luka."
    assert "coverage_gap" not in output.model_dump()
    assert "coverage_gap" not in output.model_dump_json()
    assert output.model_copy().coverage_gap == "Luka."
    assert _output().coverage_gap is None


@pytest.mark.asyncio
async def test_the_output_schemas_of_search_and_browse_stay_as_they_were() -> None:
    """Turning the private attribute into a public field changes this property set, and fails here."""
    expected = {
        "results",
        "total_count",
        "query_summary",
        "returned_count",
        "result_set_id",
        "result_set_scope",
        "page_info",
    }
    async with Client(app) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    for name in ("search_legal_acts", "browse_acts"):
        schema = tools[name].output_schema
        assert schema is not None
        data = (
            schema["$defs"]["SearchOutput"]
            if "SearchOutput" in schema.get("$defs", {})
            else schema["properties"]["data"]
        )
        assert set(data["properties"]) == expected, name
        assert "coverage" not in str(schema["properties"]).lower()
        assert "coverage" not in str(sorted(data["properties"]))
    assert set(SearchOutput.model_json_schema()["properties"]) == expected


# --- the search service ------------------------------------------------------------------------


@pytest.fixture
def clock() -> Clock:
    return Clock(TODAY)


def _service(
    client: SejmApiClient,
    coverage: CoverageRegistry | None,
    sources: ActSourceRegistry | None = None,
) -> SearchService:
    return SearchService(client, ResultStore(), sources=sources, coverage=coverage)


def _registry(clock: Clock, *providers: FakeCoverageProvider) -> CoverageRegistry:
    registry = CoverageRegistry(today=clock.today, monotonic=clock.monotonic)
    for provider in providers:
        registry.register(provider)
    return registry


@respx.mock
@pytest.mark.asyncio
async def test_a_sejm_search_past_the_newest_announcement_carries_the_gap(
    mock_client: SejmApiClient, search_results: dict, clock: Clock
) -> None:
    respx.get(SEARCH_URL).mock(return_value=Response(200, json=search_results))
    stale = segment("DU", newest=date(2026, 5, 1), newest_ref="2026/1000")
    service = _service(mock_client, _registry(clock, FakeCoverageProvider("DU", segments=(stale,))))

    output = await service.search(publisher="DU")

    assert output.coverage_gap is not None
    assert "Najnowsze ogłoszenie w źródle „Dziennik Ustaw” pochodzi z 2026-05-01 (2026/1000)" in output.coverage_gap


@respx.mock
@pytest.mark.asyncio
async def test_browse_of_a_missing_year_carries_the_year_gap(
    mock_client: SejmApiClient, search_results: dict, clock: Clock
) -> None:
    respx.get(SEARCH_URL).mock(return_value=Response(200, json=search_results))
    seg = segment("DU", years=(2020, 2021))
    service = _service(mock_client, _registry(clock, FakeCoverageProvider("DU", segments=(seg,))))

    output = await service.browse(publisher="DU", year=1999)

    assert output.coverage_gap is not None
    assert "nie ma rocznika 1999" in output.coverage_gap


@respx.mock
@pytest.mark.asyncio
async def test_a_query_inside_the_coverage_has_no_gap(
    mock_client: SejmApiClient, search_results: dict, clock: Clock
) -> None:
    respx.get(SEARCH_URL).mock(return_value=Response(200, json=search_results))
    service = _service(mock_client, _registry(clock, FakeCoverageProvider("DU")))

    assert (await service.search(publisher="DU", year=2025)).coverage_gap is None


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize("publisher", ["DU", "du", " DU "])
async def test_the_publisher_is_matched_in_any_letter_case(
    mock_client: SejmApiClient, search_results: dict, clock: Clock, publisher: str
) -> None:
    respx.get(SEARCH_URL).mock(return_value=Response(200, json=search_results))
    stale = segment("DU", newest=date(2026, 1, 1))
    service = _service(mock_client, _registry(clock, FakeCoverageProvider("DU", segments=(stale,))))

    assert (await service.search(publisher=publisher)).coverage_gap is not None


@respx.mock
@pytest.mark.asyncio
async def test_a_publisher_without_a_provider_has_no_gap_and_no_measurement(
    mock_client: SejmApiClient, search_results: dict, clock: Clock
) -> None:
    respx.get(SEARCH_URL).mock(return_value=Response(200, json=search_results))
    provider = FakeCoverageProvider("DU", segments=(segment("DU", newest=date(2020, 1, 1)),))
    service = _service(mock_client, _registry(clock, provider))

    output = await service.search(publisher="MP")

    assert output.coverage_gap is None
    assert provider.measure_calls == []


@respx.mock
@pytest.mark.asyncio
async def test_without_a_registry_the_search_is_what_it_was(mock_client: SejmApiClient, search_results: dict) -> None:
    respx.get(SEARCH_URL).mock(return_value=Response(200, json=search_results))

    output = await _service(mock_client, None).search(publisher="DU", year=1999)

    assert output.coverage_gap is None
    assert len(output.results) == 3


@respx.mock
@pytest.mark.asyncio
async def test_a_provider_that_cannot_answer_never_costs_the_search(
    mock_client: SejmApiClient, search_results: dict, clock: Clock
) -> None:
    respx.get(SEARCH_URL).mock(return_value=Response(200, json=search_results))
    broken = FakeCoverageProvider("DU", fail_measure=True, fail_fallback=True)
    service = _service(mock_client, _registry(clock, broken))

    output = await service.search(publisher="DU")

    assert output.coverage_gap is None
    assert len(output.results) == 3


@respx.mock
@pytest.mark.asyncio
async def test_a_failed_measurement_marks_the_gap_as_a_fallback(
    mock_client: SejmApiClient, search_results: dict, clock: Clock
) -> None:
    respx.get(SEARCH_URL).mock(return_value=Response(200, json=search_results))
    stale = segment("DU", newest=date(2026, 1, 1))
    service = _service(mock_client, _registry(clock, FakeCoverageProvider("DU", segments=(stale,), fail_measure=True)))

    output = await service.search(publisher="DU")

    assert output.coverage_gap is not None
    assert output.coverage_gap.endswith(FALLBACK_NOTE)


@pytest.mark.asyncio
async def test_a_source_publisher_given_by_name_is_resolved_to_its_code(
    mock_client: SejmApiClient, clock: Clock
) -> None:
    info = PublisherInfo(code="ZZ_PM", label="pomorskie", available=True, item={"code": "ZZ_PM"})
    sources = ActSourceRegistry()
    sources.register(FakeActSource(), publishers=["ZZ_PM"], info=[info])
    pm = segment("ZZ_PM", label="Dz. Urz. Woj. Pomorskiego", years=PM_YEARS)
    service = _service(mock_client, _registry(clock, FakeCoverageProvider("ZZ_PM", segments=(pm,))), sources)

    by_name = await service.search(publisher="Pomorskie", year=2007)
    by_code = await service.search(publisher="ZZ_PM", year=2007)
    browsed = await service.browse(publisher="pomorskie", year=2007)

    for output in (by_name, by_code, browsed):
        assert output.coverage_gap is not None
        assert "nie ma rocznika 2007" in output.coverage_gap


@pytest.mark.asyncio
async def test_the_provider_is_asked_once_for_a_burst_of_searches(mock_client: SejmApiClient, clock: Clock) -> None:
    sources = ActSourceRegistry()
    sources.register(FakeActSource(), publishers=["ZZ_PM"])
    provider = FakeCoverageProvider("ZZ_PM", segments=(segment("ZZ_PM"),))
    service = _service(mock_client, _registry(clock, provider), sources)

    for _ in range(3):
        await service.search(publisher="ZZ_PM")

    assert len(provider.measure_calls) == 1
