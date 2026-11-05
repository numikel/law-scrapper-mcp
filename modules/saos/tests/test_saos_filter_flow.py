"""Search, then narrow the stored set with the core `filter_results`, through the MCP protocol.

The flow an agent follows: `search_saos_judgments` stores the page it returns, `filter_results` takes the
`result_set_id` and narrows it on the shared filters, `list_result_sets` shows the kind of each set.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
import respx
from saos_testing import FakeSaos, corpus, saos_session

pytestmark = pytest.mark.asyncio

SEARCH = "search_saos_judgments"
FILTER = "filter_results"
LABOUR = "(15|18|21)$"


async def _search(session: Any, **arguments: Any) -> dict[str, Any]:
    return await session.call(SEARCH, {"limit": 41, **arguments})


def _dates(records: list[dict[str, Any]]) -> list[str]:
    return [record["judgment_date"] for record in records]


async def test_a_division_code_pattern_narrows_a_judgment_set_to_the_labour_divisions(
    saos_router: respx.MockRouter,
) -> None:
    async with saos_session(saos_router) as session:
        found = await _search(session)

        narrowed = await session.call(
            FILTER, {"result_set_id": found["result_set_id"], "pattern": LABOUR, "field": "division_code", "limit": 100}
        )

        expected = [r for r in found["results"] if r["division_code"] and re.search(LABOUR, r["division_code"])]
        assert narrowed["document_kind"] == "judgment"
        assert narrowed["results"] == []
        assert narrowed["judgments"] == expected
        assert 0 < narrowed["filtered_count"] < narrowed["original_count"] == len(found["results"])
        assert narrowed["result_set_id"] and narrowed["result_set_id"] != found["result_set_id"]
        assert narrowed["filters_applied"]


async def test_the_rest_of_the_shared_filters_work_on_judgments(saos_router: respx.MockRouter) -> None:
    async with saos_session(saos_router) as session:
        found = await _search(session)
        set_id = found["result_set_id"]
        records = found["results"]
        dated = sorted(date for date in _dates(records) if date)
        low, high = dated[len(dated) // 3], dated[2 * len(dated) // 3]
        year = int(dated[-1][:4])

        by_type = await session.call(FILTER, {"result_set_id": set_id, "type_equals": "Wyrok", "limit": 100})
        by_year = await session.call(FILTER, {"result_set_id": set_id, "year_equals": year, "limit": 100})
        by_range = await session.call(
            FILTER,
            {"result_set_id": set_id, "date_field": "judgment_date", "date_from": low, "date_to": high, "limit": 100},
        )
        ordered = await session.call(
            FILTER, {"result_set_id": set_id, "sort_by": "judgment_date", "sort_desc": False, "limit": 100}
        )

        assert {r["judgment_type"] for r in by_type["judgments"]} == {"Wyrok"}
        assert len(by_type["judgments"]) == sum(r["judgment_type"] == "Wyrok" for r in records)
        assert by_year["judgments"] and {r["judgment_date"][:4] for r in by_year["judgments"]} == {str(year)}
        assert all(low <= r["judgment_date"] <= high for r in by_range["judgments"])
        assert len(by_range["judgments"]) == sum(
            1 for r in records if r["judgment_date"] and low <= r["judgment_date"] <= high
        )
        ascending = [d for d in _dates(ordered["judgments"]) if d]
        assert ascending == sorted(ascending)
        assert len(ordered["judgments"]) == len(records)


async def test_a_narrowed_set_can_be_narrowed_again(saos_router: respx.MockRouter) -> None:
    async with saos_session(saos_router) as session:
        found = await _search(session)
        labour = await session.call(
            FILTER, {"result_set_id": found["result_set_id"], "pattern": LABOUR, "field": "division_code", "limit": 100}
        )

        sentences = await session.call(
            FILTER, {"result_set_id": labour["result_set_id"], "type_equals": "Wyrok", "limit": 100}
        )

        assert sentences["source_result_set_id"] == labour["result_set_id"]
        assert {r["judgment_type"] for r in sentences["judgments"]} == {"Wyrok"}
        assert all(re.search(LABOUR, r["division_code"]) for r in sentences["judgments"])
        assert sentences["filtered_count"] <= labour["filtered_count"]


async def test_a_pattern_that_matches_nothing_in_a_window_is_inconclusive(saos_router: respx.MockRouter) -> None:
    async with saos_session(saos_router) as session:
        found = await _search(session, limit=5, offset=10)
        assert found["result_set_scope"]["scope"] == "page"

        narrowed = await session.call(
            FILTER, {"result_set_id": found["result_set_id"], "pattern": "zzzzqqqq", "field": "signature"}
        )

        assert narrowed["judgments"] == []
        assert narrowed["result_set_id"] is None
        assert narrowed["no_match_is_inconclusive"] is True
        assert narrowed["source_scope"]["scope"] == "page"


async def test_list_result_sets_shows_the_kind_of_every_judgment_set(saos_router: respx.MockRouter) -> None:
    async with saos_session(saos_router) as session:
        found = await _search(session, limit=10)
        narrowed = await session.call(
            FILTER, {"result_set_id": found["result_set_id"], "pattern": LABOUR, "field": "division_code"}
        )

        listed = await session.call("list_result_sets", {})

        by_id = {entry["result_set_id"]: entry for entry in listed["sets"]}
        assert by_id[found["result_set_id"]]["document_kind"] == "judgment"
        assert by_id[narrowed["result_set_id"]]["document_kind"] == "judgment"
        assert by_id[found["result_set_id"]]["result_count"] == 10


async def test_a_labour_scan_set_is_filterable_like_any_other(saos_router: respx.MockRouter) -> None:
    async with saos_session(saos_router, FakeSaos(corpus())) as session:
        scanned = await session.call(SEARCH, {"labour_only": True, "limit": 10})

        narrowed = await session.call(
            FILTER, {"result_set_id": scanned["result_set_id"], "pattern": "(15|21)$", "field": "division_code"}
        )

        assert scanned["result_set_scope"]["scope"] == "page"
        assert narrowed["source_scope"]["scope"] == "page"
        assert narrowed["filtered_count"] == len(narrowed["judgments"]) > 0


async def test_an_act_only_filter_is_refused_for_judgments_in_polish(saos_router: respx.MockRouter) -> None:
    async with saos_session(saos_router) as session:
        found = await _search(session, limit=5)

        text = await session.fail(FILTER, {"result_set_id": found["result_set_id"], "status_equals": "obowiązujący"})

        assert "status_equals" in text and "orzeczenia" in text


async def test_a_field_that_judgments_do_not_have_lists_the_valid_ones(saos_router: respx.MockRouter) -> None:
    async with saos_session(saos_router) as session:
        found = await _search(session, limit=5)

        text = await session.fail(
            FILTER, {"result_set_id": found["result_set_id"], "pattern": "x", "field": "act_status"}
        )

        assert "division_code" in text and "signature" in text
