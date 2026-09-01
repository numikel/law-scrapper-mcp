"""Pagination metadata for search and browse."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from law_scrapper_mcp.models.pagination import DEFAULT_ITEM_LIMIT
from law_scrapper_mcp.services.result_store import ResultStore
from law_scrapper_mcp.services.search_service import SearchService

pytestmark = pytest.mark.asyncio


def _item(n: int) -> dict[str, Any]:
    return {
        "ELI": f"DU/2024/{n}",
        "publisher": "DU",
        "year": 2024,
        "pos": n,
        "title": f"Akt {n}",
        "status": "akt obowiązujący",
        "type": "Ustawa",
    }


def _client(payload: dict[str, Any]) -> AsyncMock:
    client = AsyncMock()
    client.get_json.return_value = payload
    return client


def _service(payload: dict[str, Any]) -> tuple[SearchService, AsyncMock]:
    client = _client(payload)
    return SearchService(client, ResultStore()), client


def _paging_service(total: int) -> tuple[SearchService, AsyncMock]:
    """A client that windows server-side, the way `acts/search` does."""
    client = AsyncMock()

    async def page(_path: str, params: dict[str, Any] | None = None, cache_ttl: int | None = None):
        window = params or {}
        start = int(window.get("offset", 0))
        end = start + int(window.get("limit", DEFAULT_ITEM_LIMIT))
        return {
            "count": max(min(end, total) - start, 0),
            "totalCount": total,
            "items": [_item(n) for n in range(start, min(end, total))],
        }

    client.get_json.side_effect = page
    return SearchService(client, ResultStore()), client


class TestSearchPage:
    async def test_page_info_describes_the_server_side_window(self) -> None:
        service, _ = _service({"items": [_item(n) for n in range(20)], "count": 1_000})

        output = await service.search(year=2024, limit=20, offset=40)

        assert output.page_info.unit == "items"
        assert output.page_info.limit == 20
        assert output.page_info.offset == 40
        assert output.page_info.returned_count == 20
        assert output.page_info.total_count == 1_000
        assert output.page_info.was_truncated is True
        assert output.page_info.next_offset == 60

    async def test_defaults_match_the_previous_behaviour(self) -> None:
        service, _ = _service({"items": [_item(n) for n in range(50)], "count": 50})

        output = await service.search(year=2024)

        assert len(output.results) == 20
        assert output.returned_count == 20
        assert output.total_count == 50
        assert output.result_set_id is not None
        assert output.page_info.limit == 20
        assert output.page_info.next_offset == 20

    async def test_limit_above_the_shared_maximum_is_not_clamped(self) -> None:
        service, _ = _service({"items": [_item(n) for n in range(150)], "count": 150})

        output = await service.search(year=2024, limit=150)

        assert output.returned_count == 150
        assert output.page_info.limit == 150
        assert output.page_info.was_truncated is False

    async def test_inconsistent_upstream_count_does_not_break_validation(self) -> None:
        """A count smaller than the payload must not turn into a ValidationError."""
        service, _ = _service({"items": [_item(n) for n in range(20)], "count": 3})

        output = await service.search(year=2024, limit=20)

        assert output.page_info.total_count == 20
        assert output.page_info.was_truncated is False
        assert output.page_info.next_offset is None

    async def test_empty_result_reports_an_untruncated_zero_page(self) -> None:
        service, _ = _service({"items": [], "count": 0})

        output = await service.search(year=2024)

        assert output.results == []
        assert output.result_set_id is None
        assert output.page_info.total_count == 0
        assert output.page_info.was_truncated is False

    async def test_negative_offset_does_not_silently_drop_leading_results(self) -> None:
        """A negative offset must be treated as zero, not as a hidden backward shift."""
        service, _ = _service({"items": [_item(n) for n in range(20)], "count": 20})

        negative = await service.search(year=2024, limit=20, offset=-5)
        zero = await service.search(year=2024, limit=20, offset=0)

        assert [act.eli for act in negative.results] == [act.eli for act in zero.results]
        assert negative.returned_count == 20
        assert negative.page_info.offset == 0


class TestBrowsePage:
    async def test_browse_pages_come_from_the_api_window(self) -> None:
        """The API skips the records; browse must not skip them a second time."""
        service, _ = _paging_service(50)

        first = await service.browse("DU", 2024, limit=10, offset=0)
        second = await service.browse("DU", 2024, limit=10, offset=10)

        assert [act.eli for act in first.results] == [f"DU/2024/{n}" for n in range(10)]
        assert [act.eli for act in second.results] == [f"DU/2024/{n}" for n in range(10, 20)]

    async def test_next_offset_is_servable_by_browse(self) -> None:
        service, _ = _paging_service(50)

        first = await service.browse("DU", 2024, limit=10)
        assert first.page_info.next_offset == 10

        second = await service.browse("DU", 2024, limit=10, offset=first.page_info.next_offset)

        assert second.results[0].eli == "DU/2024/10"

    async def test_browse_does_not_issue_extra_upstream_requests_for_a_page(self) -> None:
        service, client = _paging_service(50)

        await service.browse("DU", 2024, limit=10, offset=30)

        assert client.get_json.await_count == 1

    async def test_the_requested_window_reaches_the_api(self) -> None:
        """The point of F30: the window is a query parameter, not a local slice."""
        service, client = _paging_service(1984)

        await service.browse("DU", 2024, limit=10, offset=30)

        params = client.get_json.await_args.kwargs["params"]
        assert (params["limit"], params["offset"]) == (10, 30)

    async def test_offset_past_the_end_returns_an_empty_untruncated_page(self) -> None:
        service, _ = _paging_service(5)

        output = await service.browse("DU", 2024, limit=10, offset=99)

        assert output.results == []
        assert output.page_info.total_count == 5
        assert output.page_info.was_truncated is False
