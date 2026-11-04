"""`labour_only`: the bounded scan, its cursor, its caps, its refusals and what it stores.

Every list below is built by `derived_list` from recorded search items; no SAOS record is invented and no
test sends a request (the autouse network guard in `conftest.py` would stop it).
"""

from __future__ import annotations

from typing import Any

import jsonschema
import pytest
import respx
from saos_testing import (
    TODAY,
    FakeSaos,
    Harness,
    HarnessFactory,
    connected,
    derived_list,
    supreme_chamber_items,
)

from law_scrapper_mcp.client.exceptions import ApiUnavailableError
from law_scrapper_mcp.models.tool_outputs import DocumentKind, Hint, SetScope
from law_scrapper_mcp.services.result_store import ResultSetTooLargeError
from law_scrapper_mcp_saos.service import SCAN_MAX_PAGES, SCAN_PAGE_SIZE, TOOL_SEARCH

pytestmark = pytest.mark.asyncio

# A labour record at every seventh position of a list of 1050 records.
SPREAD = ("L" + "." * 6) * 150
SPREAD_ITEMS = derived_list(SPREAD)
SPREAD_LABOUR_IDS = [str(item["id"]) for index, item in enumerate(SPREAD_ITEMS) if index % 7 == 0]


def _ids(records: Any) -> list[str]:
    return [str(record.source_id) for record in records]


def _hint(hints: list[Hint], tool: str) -> Hint:
    return next(hint for hint in hints if hint.tool == tool)


class TestCursor:
    async def test_the_scan_stops_on_the_limit_and_the_cursor_is_right_after_the_last_hit(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(SPREAD_ITEMS))

        response = await harness.service.search(labour_only=True, limit=20)

        scan = response.data.scan
        assert scan is not None
        assert _ids(response.data.results) == SPREAD_LABOUR_IDS[:20]
        assert harness.fake.pages == [0, 1]
        assert all(call["pageSize"] == "100" for call in harness.fake.calls)
        assert (scan.scanned_from, scan.scanned_to, scan.next_offset) == (0, 134, 134)
        assert (scan.pages_fetched, scan.saos_records_scanned) == (2, 134)
        assert scan.scan_complete is False
        assert scan.interrupted is False

    async def test_resuming_from_the_cursor_neither_skips_nor_repeats_a_record(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(SPREAD_ITEMS))
        first = await harness.service.search(labour_only=True, limit=20)
        assert first.data.scan is not None

        second = await harness.service.search(labour_only=True, limit=20, offset=first.data.scan.next_offset)

        assert _ids(first.data.results) + _ids(second.data.results) == SPREAD_LABOUR_IDS[:40]
        assert second.data.scan is not None
        assert second.data.scan.scanned_from == 134
        # Position 134 lies in SAOS page 1, 34 records in; page 1 came from the cache, page 2 is new.
        assert harness.fake.pages == [0, 1, 2]

    async def test_the_hint_gives_the_exact_next_call_and_following_it_continues_the_scan(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(SPREAD_ITEMS))
        first = await harness.service.search(phrase="wypadek", labour_only=True, limit=20)

        hint = next(h for h in first.hints if h.tool == TOOL_SEARCH and h.parameters.get("labour_only"))
        followed = await harness.service.search(**hint.parameters)

        assert hint.parameters["phrase"] == "wypadek"
        assert (hint.parameters["limit"], hint.parameters["offset"]) == (20, 134)
        assert _ids(first.data.results) + _ids(followed.data.results) == SPREAD_LABOUR_IDS[:40]

    async def test_the_scan_gives_up_after_five_pages_and_returns_a_cursor_with_no_hits(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(derived_list("." * 1200 + "L")))

        response = await harness.service.search(labour_only=True, limit=20)

        scan = response.data.scan
        assert scan is not None
        assert SCAN_MAX_PAGES == 5 and SCAN_PAGE_SIZE == 100
        assert harness.fake.pages == [0, 1, 2, 3, 4]
        assert response.data.results == []
        assert response.data.result_set_id is None
        assert (scan.pages_fetched, scan.scanned_to, scan.next_offset, scan.scan_complete) == (5, 500, 500, False)
        assert await harness.store.list_sets() == []
        hint = next(h for h in response.hints if (h.parameters or {}).get("labour_only"))
        assert hint.parameters["offset"] == 500
        assert "pozycji 500" in hint.message

    async def test_walking_the_whole_list_by_the_cursor_finds_every_hit_once_and_ends_with_no_cursor(
        self, make_harness: HarnessFactory
    ) -> None:
        items = derived_list("." * 1200 + "L")
        harness = make_harness(FakeSaos(items))
        found: list[str] = []
        offset: int | None = 0
        calls = 0

        while offset is not None:
            response = await harness.service.search(labour_only=True, limit=20, offset=offset)
            assert response.data.scan is not None
            found += _ids(response.data.results)
            offset = response.data.scan.next_offset
            calls += 1

        assert found == [str(items[-1]["id"])]
        assert calls == 3
        assert harness.fake.pages == list(range(13))  # 0..12, each page read once, the rest from the cache
        assert response.data.scan.scan_complete is True and response.data.scan.next_offset is None

    async def test_the_end_of_the_list_completes_the_scan(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos(derived_list("L" + "." * 249)))

        response = await harness.service.search(labour_only=True, limit=20)

        scan = response.data.scan
        assert scan is not None
        assert harness.fake.pages == [0, 1, 2]
        assert len(response.data.results) == 1
        assert (scan.scanned_to, scan.next_offset, scan.scan_complete) == (250, None, True)
        assert all(not (h.parameters or {}).get("labour_only") for h in response.hints)

    async def test_a_list_that_ends_on_a_page_boundary_costs_no_extra_request(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(derived_list("L" + "." * 199)))

        response = await harness.service.search(labour_only=True, limit=20)

        assert harness.fake.pages == [0, 1]
        assert response.data.scan is not None and response.data.scan.scan_complete is True

    async def test_stopping_on_the_last_record_of_the_list_completes_the_scan(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(derived_list("." * 9 + "L")))

        response = await harness.service.search(labour_only=True, limit=1)

        assert response.data.scan is not None
        assert (response.data.scan.next_offset, response.data.scan.scan_complete) == (None, True)

    async def test_an_offset_past_the_end_returns_an_empty_finished_scan(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos(derived_list("L" + "." * 249)))

        response = await harness.service.search(labour_only=True, limit=20, offset=900)

        scan = response.data.scan
        assert scan is not None
        assert harness.fake.pages == [9]
        assert response.data.results == []
        assert (scan.scanned_from, scan.scanned_to, scan.next_offset, scan.scan_complete) == (900, 900, None, True)
        assert any("za końcem listy" in h.message for h in response.hints)

    async def test_a_record_repeated_at_the_seam_of_two_pages_counts_once(self, make_harness: HarnessFactory) -> None:
        items = derived_list("L" * 30 + "." * 69 + "L" + "L" * 10)
        items[100] = items[99]  # SAOS inserted a record between two requests
        harness = make_harness(FakeSaos(items))

        response = await harness.service.search(labour_only=True, limit=100)

        ids = _ids(response.data.results)
        assert response.data.scan is not None and response.data.scan.scan_complete is True
        assert len(ids) == len(set(ids)) == 40


class TestFailures:
    async def test_the_first_page_failing_fails_the_call(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos(SPREAD_ITEMS, failing_pages={0}))

        with pytest.raises(ApiUnavailableError) as excinfo:
            await harness.service.search(labour_only=True)

        assert excinfo.value.source == "SAOS"
        assert await harness.store.list_sets() == []

    async def test_a_later_page_failing_returns_the_hits_so_far_with_a_cursor_at_the_failed_page(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(derived_list(("L" + "." * 3) * 50), failing_pages={1}))

        response = await harness.service.search(labour_only=True, limit=100)

        scan = response.data.scan
        assert scan is not None
        assert len(response.data.results) == 25  # the labour records of the first page
        assert scan.interrupted is True and scan.scan_complete is False
        assert (scan.pages_fetched, scan.scanned_to, scan.next_offset) == (1, 100, 100)
        assert response.data.result_set_id is not None
        assert any("nie odpowiedziała" in h.message and h.parameters["offset"] == 100 for h in response.hints)


class TestRule:
    async def test_division_codes_ending_in_15_18_and_21_count_and_others_and_missing_divisions_do_not(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(derived_list("L58.-L.")))

        response = await harness.service.search(labour_only=True)

        codes = [record.division_code for record in response.data.results]
        assert [code[-2:] for code in codes if code] == ["21", "15", "18", "21"]
        assert _ids(response.data.results) == [
            str(item["id"]) for i, item in enumerate(harness.fake.items) if i in (0, 1, 2, 5)
        ]

    async def test_the_scan_asks_for_common_courts_when_no_court_type_is_given(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(SPREAD_ITEMS))

        await harness.service.search(labour_only=True, limit=1)
        await harness.service.search(labour_only=True, limit=1, court_type="common", offset=500)

        assert [call["courtType"] for call in harness.fake.calls] == ["COMMON", "COMMON"]
        assert harness.fake.calls[0]["judgmentDateTo"] == TODAY.isoformat()
        assert harness.fake.calls[0]["sortingField"] == "JUDGMENT_DATE"

    async def test_the_response_states_the_rule_and_the_narrowing_pattern(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos(SPREAD_ITEMS))

        response = await harness.service.search(labour_only=True, limit=5)

        assert response.data.scan is not None
        assert "(15|18|21)$" in response.data.scan.rule
        assert "(15|21)$" in response.data.scan.rule
        narrowing = _hint(response.hints, "filter_results")
        assert narrowing.parameters["pattern"] == "(15|21)$"
        assert narrowing.parameters["result_set_id"] == response.data.result_set_id


class TestRefusals:
    @pytest.mark.parametrize("court_type", ["CONSTITUTIONAL_TRIBUNAL", "NATIONAL_APPEAL_CHAMBER", "ADMINISTRATIVE"])
    async def test_other_court_types_are_refused_before_any_request(
        self, make_harness: HarnessFactory, court_type: str
    ) -> None:
        harness = make_harness(FakeSaos(SPREAD_ITEMS))

        with pytest.raises(ValueError, match="tylko dla sądów powszechnych") as excinfo:
            await harness.service.search(labour_only=True, court_type=court_type)

        assert court_type in str(excinfo.value)
        assert harness.fake.requests == 0

    async def test_the_supreme_court_is_still_searchable_without_the_flag(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        response = await harness.service.search(court_type="SUPREME", limit=3)

        assert response.data.scan is None
        assert response.data.page_info is not None

    async def test_the_combination_with_portal_links_is_refused_before_any_request(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(SPREAD_ITEMS))

        with pytest.raises(ValueError, match="nie łączą się") as excinfo:
            await harness.service.search(labour_only=True, include_portal_links=True)

        assert "get_saos_judgment_details" in str(excinfo.value)
        assert harness.fake.requests == 0

    async def test_a_malformed_flag_is_a_polish_error(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos(SPREAD_ITEMS))

        with pytest.raises(ValueError, match="labour_only"):
            await harness.service.search(labour_only="maybe")

        assert harness.fake.requests == 0

    @pytest.mark.parametrize("flag", ["true", "TAK", "1", "yes", True])
    async def test_the_flag_is_accepted_as_a_boolean_or_as_text(
        self, make_harness: HarnessFactory, flag: bool | str
    ) -> None:
        harness = make_harness(FakeSaos(SPREAD_ITEMS))

        response = await harness.service.search(labour_only=flag, limit=1)

        assert response.data.scan is not None


def _supreme_list(copies: int = 1) -> list[dict[str, Any]]:
    """The recorded Supreme Court page, repeated `copies` times with fresh ids (newest first stays as recorded)."""
    return [
        {**item, "id": 9_500_000 + copy * 1000 + index}
        for copy in range(copies)
        for index, item in enumerate(supreme_chamber_items())
    ]


LABOUR_CHAMBER_POSITIONS = [
    index
    for index, item in enumerate(supreme_chamber_items())
    if item["division"]["chambers"][0]["name"].startswith("Izba Pracy")
]


class TestSupremeCourt:
    async def test_the_scan_keeps_the_records_of_the_labour_chamber_and_only_those(
        self, make_harness: HarnessFactory
    ) -> None:
        items = _supreme_list()
        harness = make_harness(FakeSaos(items))

        response = await harness.service.search(labour_only=True, court_type="SUPREME", limit=100)

        scan = response.data.scan
        assert scan is not None
        assert len(LABOUR_CHAMBER_POSITIONS) == 15
        assert _ids(response.data.results) == [str(items[i]["id"]) for i in LABOUR_CHAMBER_POSITIONS]
        assert all("Izba Pracy" in (r.division or "") for r in response.data.results)
        assert [call["courtType"] for call in harness.fake.calls] == ["SUPREME"]
        assert harness.fake.pages == [0]
        assert (scan.scanned_to, scan.next_offset, scan.scan_complete) == (100, None, True)

    async def test_the_response_states_the_chamber_rule_and_not_the_division_code_rule(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(_supreme_list()))

        response = await harness.service.search(labour_only=True, court_type="supreme", limit=5)

        assert response.data.scan is not None
        assert "Izba Pracy" in response.data.scan.rule
        assert "division_code" not in response.data.scan.rule
        assert all(h.parameters is None or h.parameters.get("pattern") != "(15|21)$" for h in response.hints)

    async def test_the_cursor_and_the_limit_work_as_for_common_courts(self, make_harness: HarnessFactory) -> None:
        items = _supreme_list()
        harness = make_harness(FakeSaos(items))

        first = await harness.service.search(labour_only=True, court_type="SUPREME", limit=5)
        assert first.data.scan is not None
        second = await harness.service.search(
            labour_only=True, court_type="SUPREME", limit=5, offset=first.data.scan.next_offset
        )

        every = [str(items[i]["id"]) for i in LABOUR_CHAMBER_POSITIONS]
        assert _ids(first.data.results) == every[:5]
        assert first.data.scan.next_offset == LABOUR_CHAMBER_POSITIONS[4] + 1
        assert _ids(second.data.results) == every[5:10]

    async def test_the_five_page_cap_holds_on_a_long_list(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos(_supreme_list(copies=6)))

        response = await harness.service.search(labour_only=True, court_type="SUPREME", limit=100)

        scan = response.data.scan
        assert scan is not None
        assert harness.fake.pages == [0, 1, 2, 3, 4]
        assert len(response.data.results) == 5 * 15
        assert (scan.pages_fetched, scan.next_offset, scan.scan_complete) == (5, 500, False)

    async def test_the_documented_narrowing_selects_the_same_records_as_the_scan(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(_supreme_list()))
        plain = await harness.service.search(court_type="SUPREME", limit=100)
        scanned = await harness.service.search(labour_only=True, court_type="SUPREME", limit=100)

        narrowed = await harness.store.filter_and_store(
            plain.data.result_set_id or "", pattern="Izba Pracy", field="division"
        )

        assert [j.source_id for j in narrowed.judgments] == _ids(scanned.data.results)
        hint = next(h for h in plain.hints if h.tool == "filter_results" and h.parameters)
        assert (hint.parameters or {})["pattern"] == "Izba Pracy"
        assert (hint.parameters or {})["field"] == "division"

    async def test_a_scan_without_a_court_type_never_reads_the_supreme_court(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(_supreme_list()))

        await harness.service.search(labour_only=True)

        assert [call["courtType"] for call in harness.fake.calls] == ["COMMON"]


class TestRequestBudget:
    @pytest.mark.parametrize("limit", [1, 20, 100])
    @pytest.mark.parametrize("offset", [0, 37, 100, 399, 950])
    async def test_a_scan_never_sends_more_than_five_requests_and_never_asks_for_details(
        self, make_harness: HarnessFactory, limit: int, offset: int
    ) -> None:
        harness = make_harness(FakeSaos(derived_list("." * 2000)))

        await harness.service.search(labour_only=True, limit=limit, offset=offset)

        assert 1 <= len(harness.fake.calls) <= SCAN_MAX_PAGES
        assert harness.fake.detail_urls == []

    async def test_repeating_a_scan_is_served_from_the_cache(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos(SPREAD_ITEMS))

        await harness.service.search(labour_only=True, limit=20)
        await harness.service.search(labour_only=True, limit=20)

        assert harness.fake.pages == [0, 1]


class TestStoredSet:
    async def test_a_scan_is_stored_as_a_page_scope_judgment_set_of_its_hits(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(SPREAD_ITEMS))

        response = await harness.service.search(labour_only=True, limit=20, offset=134)

        stored = await harness.store.get(response.data.result_set_id or "")
        assert stored is not None
        assert stored.document_kind is DocumentKind.JUDGMENT
        assert stored.scope is SetScope.PAGE
        assert (stored.window_offset, stored.total_count, len(stored.results)) == (134, 20, 20)
        scope = response.data.result_set_scope
        assert scope is not None and scope.scope is SetScope.PAGE
        assert response.data.total_count is None and response.data.page_info is None
        assert response.data.returned_count == 20
        assert "labour_only=skan pozycji SAOS od 134 do 274" in response.data.query_summary

    async def test_scope_an_empty_narrowing_of_a_scan_set_is_inconclusive(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos(SPREAD_ITEMS))
        response = await harness.service.search(labour_only=True, limit=20)

        output = await harness.store.filter_and_store(response.data.result_set_id or "", pattern="zzzzqqqq")

        assert output.judgments == []
        assert output.no_match_is_inconclusive is True

    async def test_a_scan_set_can_be_narrowed_to_the_divisions_that_are_labour_only(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(derived_list("L58L")))
        response = await harness.service.search(labour_only=True)

        output = await harness.store.filter_and_store(
            response.data.result_set_id or "", pattern="(15|21)$", field="division_code"
        )

        assert [j.division_code[-2:] for j in output.judgments if j.division_code] == ["21", "15", "21"]


class TestFilterLimit:
    async def test_a_scan_set_above_the_filter_limit_comes_with_the_repeat_call(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(SPREAD_ITEMS), filter_max_records=3)

        response = await harness.service.search(labour_only=True, limit=5, offset=134)

        hint = next(h for h in response.hints if "przyjmuje najwyżej" in h.message)
        assert hint.tool == TOOL_SEARCH
        assert hint.parameters["labour_only"] is True
        assert (hint.parameters["limit"], hint.parameters["offset"]) == (3, 134)

    async def test_that_set_really_is_refused_by_the_core_filter(self, make_harness: HarnessFactory) -> None:
        harness: Harness = make_harness(FakeSaos(SPREAD_ITEMS))
        harness.store.max_records = 3

        response = await harness.service.search(labour_only=True, limit=5)

        with pytest.raises(ResultSetTooLargeError):
            await harness.store.filter_and_store(response.data.result_set_id or "", pattern="x")

    async def test_a_set_within_the_limit_has_no_such_hint(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos(SPREAD_ITEMS), filter_max_records=5)

        response = await harness.service.search(labour_only=True, limit=5)

        assert all("przyjmuje najwyżej" not in h.message for h in response.hints)


class TestThroughTheProtocol:
    async def test_a_labour_call_validates_against_the_output_schema_and_has_a_cursor_not_pages(
        self, saos_router: respx.MockRouter
    ) -> None:
        async with connected(saos_router, FakeSaos(SPREAD_ITEMS)) as client:
            tool = next(t for t in (await client.list_tools()).tools if t.name == TOOL_SEARCH)

            result = await client.call_tool(TOOL_SEARCH, {"labour_only": True, "limit": 10})

            assert result.is_error is False
            payload = result.structured_content
            assert payload is not None
            jsonschema.validate(payload, tool.output_schema)
            data = payload["data"]
            assert data["total_count"] is None and data["page_info"] is None
            assert data["returned_count"] == 10 == len(data["results"])
            assert data["scan"]["next_offset"] == 64
            assert data["scan"]["rule"]
            assert data["result_set_scope"]["scope"] == "page"

    async def test_a_plain_call_still_has_pages_and_no_scan(self, saos_router: respx.MockRouter) -> None:
        async with connected(saos_router, FakeSaos(SPREAD_ITEMS)) as client:
            tool = next(t for t in (await client.list_tools()).tools if t.name == TOOL_SEARCH)

            result = await client.call_tool(TOOL_SEARCH, {"limit": 5})

            payload = result.structured_content
            assert payload is not None
            jsonschema.validate(payload, tool.output_schema)
            assert payload["data"]["scan"] is None
            assert payload["data"]["page_info"]["next_offset"] == 5
            assert payload["data"]["total_count"] == len(SPREAD_ITEMS)

    @pytest.mark.parametrize(
        ("arguments", "text"),
        [
            ({"labour_only": True, "court_type": "NATIONAL_APPEAL_CHAMBER"}, "Najwyższego"),
            ({"labour_only": True, "court_type": "ADMINISTRATIVE"}, "tylko dla sądów powszechnych"),
            ({"labour_only": True, "include_portal_links": True}, "nie łączą się"),
        ],
    )
    async def test_a_refusal_is_a_polish_tool_error_and_sends_nothing(
        self, saos_router: respx.MockRouter, arguments: dict[str, Any], text: str
    ) -> None:
        fake = FakeSaos(SPREAD_ITEMS)
        async with connected(saos_router, fake) as client:
            result = await client.call_tool(TOOL_SEARCH, arguments)

        assert result.is_error is True
        assert text in result.content[0].text  # type: ignore[union-attr]
        assert fake.requests == 0
