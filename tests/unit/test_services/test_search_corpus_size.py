"""`search()` reports the corpus size and pays for one page (D1, D7).

Before this change `search()` read `count` — the size of the returned page — as
`total_count`, so a query matching 1984 acts answered "twenty" and produced
`was_truncated=False`. It also sent `limit` only when the caller supplied one, so a
default call downloaded whatever page the API felt like building.

The size of that default page is measured, not assumed: see
`tests/fixtures/search_default_page.provenance.md` — 709 437 B, 500 records.
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
from law_scrapper_mcp.services.result_store import ResultStore
from law_scrapper_mcp.services.search_service import SearchService

SEARCH_URL = "https://api.sejm.gov.pl/eli/acts/search"


@pytest.fixture
def browse_page() -> dict[str, Any]:
    """The recorded `acts/search` response: count=5, totalCount=1984."""
    path = Path(__file__).parents[2] / "fixtures" / "browse_page.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest_asyncio.fixture
async def service() -> AsyncGenerator[SearchService]:
    client = SejmApiClient(cache=TTLCache(max_entries=100), timeout=5.0)
    await client.start()
    yield SearchService(client, ResultStore())
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_total_count_is_the_corpus_not_the_page(service: SearchService, browse_page: dict[str, Any]) -> None:
    """Criterion 5. Before D1 this returned total_count=5 and was_truncated=False."""
    respx.get(SEARCH_URL).mock(return_value=Response(200, json=browse_page))

    output = await service.search(year=2024, limit=5)

    assert output.total_count == 1984
    assert output.page_info.total_count == 1984
    assert output.page_info.was_truncated is True
    assert output.page_info.next_offset == 5


@pytest.mark.asyncio
@respx.mock
async def test_a_response_without_total_count_still_reads_count(service: SearchService) -> None:
    """Criterion 6. The fallback chain keeps every existing pagination test honest."""
    payload = {
        "count": 3,
        "items": [
            {
                "ELI": f"DU/2024/{n}",
                "publisher": "DU",
                "year": 2024,
                "pos": n,
                "title": f"Akt {n}",
                "status": "akt obowiązujący",
                "type": "Ustawa",
            }
            for n in range(3)
        ],
    }
    respx.get(SEARCH_URL).mock(return_value=Response(200, json=payload))

    output = await service.search(year=2024, limit=3)

    assert output.total_count == 3
    assert output.page_info.was_truncated is False


@pytest.mark.asyncio
@respx.mock
async def test_a_default_call_asks_for_twenty_records(service: SearchService, browse_page: dict[str, Any]) -> None:
    """Criterion 7. Without this the API builds a 500-record page (measured)."""
    route = respx.get(SEARCH_URL).mock(return_value=Response(200, json=browse_page))

    await service.search(year=2024)

    assert route.called
    assert route.calls.last.request.url.params["limit"] == "20"


@pytest.mark.asyncio
@respx.mock
async def test_an_explicit_limit_is_not_clamped(service: SearchService) -> None:
    """Criterion 8. `search_legal_acts` stays the one list tool without a ceiling."""
    payload = {
        "count": 150,
        "totalCount": 150,
        "items": [
            {
                "ELI": f"DU/2024/{n}",
                "publisher": "DU",
                "year": 2024,
                "pos": n,
                "title": f"Akt {n}",
                "status": "akt obowiązujący",
                "type": "Ustawa",
            }
            for n in range(150)
        ],
    }
    route = respx.get(SEARCH_URL).mock(return_value=Response(200, json=payload))

    output = await service.search(year=2024, limit=150)

    assert route.calls.last.request.url.params["limit"] == "150"
    assert output.returned_count == 150


def test_the_default_page_measurement_is_on_record() -> None:
    """Criterion 9. D7 rests on a measurement; deleting it would strand the claim."""
    path = Path(__file__).parents[2] / "fixtures" / "search_default_page.provenance.md"
    text = path.read_text(encoding="utf-8")

    assert "709 437 B" in text
    assert "count=500" in text
    assert "totalCount=1984" in text


@pytest.mark.asyncio
@respx.mock
async def test_a_negative_offset_never_reaches_the_wire(service: SearchService, browse_page: dict[str, Any]) -> None:
    """Issue #18: `if offset:` forwarded -5 verbatim while `_output` treated it as zero.

    The tool rejects negatives before they get here; the service is the second line
    and must not ask api.sejm.gov.pl a question it would not ask itself.
    """
    route = respx.get(SEARCH_URL).mock(return_value=Response(200, json=browse_page))

    output = await service.search(year=2024, limit=5, offset=-5)

    assert "offset" not in route.calls.last.request.url.params
    assert output.page_info.offset == 0


@pytest.mark.asyncio
@respx.mock
async def test_a_zero_limit_search_still_asks_upstream_for_one_record(
    service: SearchService, browse_page: dict[str, Any]
) -> None:
    """Issue #55: the floor in `search()` mirrors `browse()` and is pinned the same way.

    The tool rejects `limit=0` since #18; the service is still reachable with it and
    must neither send `limit=0` (what the endpoint does with that is unverified) nor
    lose the corpus size. One record is fetched and `_output` slices it away.
    """
    route = respx.get(SEARCH_URL).mock(return_value=Response(200, json=browse_page))

    output = await service.search(year=2024, limit=0)

    assert route.calls.last.request.url.params["limit"] == "1"
    assert output.results == []
    assert output.page_info.limit == 0
    assert output.page_info.total_count == 1984
