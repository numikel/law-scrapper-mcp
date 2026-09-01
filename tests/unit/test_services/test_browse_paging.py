"""browse() fetches one page from the search endpoint, not a whole year (finding F30).

The year endpoint `acts/{publisher}/{year}` ignores `limit` and `offset` — a probe on
2026-08-31 got byte-identical 1 093 224 B responses with and without them — while
`acts/search` honours both, preserves the same order and reports the same `totalCount`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import respx
from httpx import Response

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.models.enums import DetailLevel
from law_scrapper_mcp.services.result_store import ResultStore
from law_scrapper_mcp.services.search_service import SearchService

pytestmark = pytest.mark.asyncio

SEARCH_URL = "https://api.sejm.gov.pl/eli/acts/search"
YEAR_URL = "https://api.sejm.gov.pl/eli/acts/DU/2024"

# Field set of ActSummaryOutput before this change. Criterion 10: the search endpoint
# returns a superset of the year endpoint's fields, and none of the extras may reach
# the tool response — that would be a contract change (design constraint 1).
EXPECTED_FIELDS = {
    "eli",
    "publisher",
    "year",
    "pos",
    "title",
    "status",
    "type",
    "promulgation_date",
    "effective_date",
    "in_force",
}


@pytest.fixture
def browse_page() -> dict[str, Any]:
    path = Path(__file__).parents[2] / "fixtures" / "browse_page.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest_asyncio.fixture
async def service() -> AsyncGenerator[SearchService]:
    client = SejmApiClient(cache=TTLCache(max_entries=100), timeout=5.0)
    await client.start()
    yield SearchService(client, ResultStore())
    await client.close()


@respx.mock
async def test_browse_fetches_one_page_from_the_search_endpoint(
    service: SearchService, browse_page: dict[str, Any]
) -> None:
    """Criterion 6: exactly one request, to acts/search, carrying limit and offset.

    At `offset=0`, `offset` is omitted from the params dict entirely — matching
    `search()`'s own `if offset:` idiom — so the two methods build identical
    parameter dicts (and therefore the same cache key) for the default first page.
    """
    route = respx.get(SEARCH_URL).mock(return_value=Response(200, json=browse_page))
    year_route = respx.get(YEAR_URL).mock(return_value=Response(200, json={"items": []}))

    await service.browse("DU", 2024, limit=20, offset=0)

    assert route.call_count == 1
    assert year_route.call_count == 0
    params = route.calls.last.request.url.params
    assert params["publisher"] == "DU"
    assert params["year"] == "2024"
    assert params["limit"] == "20"
    assert "offset" not in params


@respx.mock
async def test_total_count_is_the_year_not_the_page(service: SearchService, browse_page: dict[str, Any]) -> None:
    """Criterion 7: `count` is this page (5), `totalCount` is the year (1984).

    Copying search()'s `data.get("count")` here would report a 1984-act year as five
    acts and call the first page complete.
    """
    respx.get(SEARCH_URL).mock(return_value=Response(200, json=browse_page))

    output = await service.browse("DU", 2024, limit=5, offset=10)

    assert output.total_count == 1984
    assert output.page_info.total_count == 1984
    assert output.returned_count == 5


@respx.mock
async def test_first_page_of_a_large_year_reports_a_next_offset(
    service: SearchService, browse_page: dict[str, Any]
) -> None:
    """Criterion 8: the default first page of DU/2024 is truncated and servable."""
    page = dict(browse_page, count=20, items=browse_page["items"] * 4)
    route = respx.get(SEARCH_URL).mock(return_value=Response(200, json=page))

    output = await service.browse("DU", 2024)

    assert route.calls.last.request.url.params["limit"] == "20"
    assert output.page_info.limit == 20
    assert output.page_info.offset == 0
    assert output.page_info.returned_count == 20
    assert output.page_info.was_truncated is True
    assert output.page_info.next_offset == 20


@respx.mock
async def test_the_api_window_is_not_sliced_a_second_time(service: SearchService, browse_page: dict[str, Any]) -> None:
    """Criterion 9: the API already skipped 10 records; slicing locally would skip 10 more."""
    respx.get(SEARCH_URL).mock(return_value=Response(200, json=browse_page))

    output = await service.browse("DU", 2024, limit=5, offset=10)

    # Derived from the fixture rather than copied out of it: `browse_page.json` is a
    # recording of the live endpoint (see its provenance note), so refreshing it must not
    # cost a test edit. The invariant is that every record the API handed back survives
    # in order — a second local slice would drop the first five.
    assert [act.pos for act in output.results] == [item["pos"] for item in browse_page["items"]]


@respx.mock
async def test_the_richer_response_does_not_leak_new_fields(
    service: SearchService, browse_page: dict[str, Any]
) -> None:
    """Criterion 10: keywords/entryIntoForce/references stay out of the tool response."""
    respx.get(SEARCH_URL).mock(return_value=Response(200, json=browse_page))

    output = await service.browse("DU", 2024, limit=5, offset=10, detail_level=DetailLevel.FULL)

    assert set(output.results[0].model_dump().keys()) == EXPECTED_FIELDS


@respx.mock
async def test_browse_reuses_the_cache_entry_written_by_search(
    service: SearchService, browse_page: dict[str, Any]
) -> None:
    """Criterion 11 (D8): one API question, one cache entry, one request.

    Both calls now hit `acts/search` with the same parameters, so they share the key
    `json:acts/search:{...}`. That is accepted deliberately: separating the keys would
    fetch the same year twice, which is the opposite of what this cluster is for. Both
    `search()` and `browse()` omit `offset` from the params dict when it is falsy, so
    the dicts coincide at this non-zero offset too — the zero-offset case (the more
    common one) is covered separately below.
    """
    route = respx.get(SEARCH_URL).mock(return_value=Response(200, json=browse_page))

    searched = await service.search(publisher="DU", year=2024, limit=5, offset=10)
    browsed = await service.browse("DU", 2024, limit=5, offset=10)

    assert route.call_count == 1
    assert [act.eli for act in browsed.results] == [act.eli for act in searched.results]


@respx.mock
async def test_browse_reuses_the_cache_entry_written_by_search_at_the_default_offset(
    service: SearchService, browse_page: dict[str, Any]
) -> None:
    """Criterion 11 (D8) at the most common call shape: the default first page.

    `offset=0` is the default for both tools, so this is the single most likely
    browse+search overlap in real use — and the one the fix in I5 exists for.
    """
    route = respx.get(SEARCH_URL).mock(return_value=Response(200, json=browse_page))

    searched = await service.search(publisher="DU", year=2024, limit=20)
    browsed = await service.browse("DU", 2024, limit=20)

    assert route.call_count == 1
    assert [act.eli for act in browsed.results] == [act.eli for act in searched.results]


@respx.mock
async def test_a_zero_limit_page_still_reports_the_year_size(
    service: SearchService, browse_page: dict[str, Any]
) -> None:
    """Spec risk 5: `limit=0` must not probe an undefined API corner or lose the total.

    `_output` accepts a zero page limit, so the observable answer is an empty page
    with a truthful `total_count`. We ask the API for one record and slice it away,
    rather than sending `limit=0` and finding out what the endpoint does with it.
    """
    route = respx.get(SEARCH_URL).mock(return_value=Response(200, json=browse_page))

    output = await service.browse("DU", 2024, limit=0)

    assert route.calls.last.request.url.params["limit"] == "1"
    assert output.results == []
    assert output.page_info.limit == 0
    assert output.page_info.total_count == 1984
