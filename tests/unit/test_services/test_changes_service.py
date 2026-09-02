"""Tests for ChangesService."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx
from httpx import Response

from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.models.pagination import PageUnit
from law_scrapper_mcp.services.changes_service import ChangesService
from law_scrapper_mcp.services.result_store import ResultStore

pytestmark = pytest.mark.asyncio

SEARCH_URL = "https://api.sejm.gov.pl/eli/acts/search"


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


def _windowed(items: list[dict[str, Any]]) -> Callable[[httpx.Request], Response]:
    """A search endpoint that honours `limit` and `offset`, the way `acts/search` does."""

    def respond(request: httpx.Request) -> Response:
        params = request.url.params
        start = int(params.get("offset", 0))
        end = start + int(params.get("limit", 20))
        page = items[start:end]
        return Response(200, json={"count": len(page), "totalCount": len(items), "items": page})

    return respond


class TestChangesService:
    """Tests for changes tracking service."""

    @pytest.fixture
    def service(self, mock_client: SejmApiClient) -> ChangesService:
        """Create ChangesService instance."""
        return ChangesService(client=mock_client, result_store=ResultStore())

    @respx.mock
    async def test_track_changes_basic(self, service: ChangesService, search_results: dict):
        """Test basic changes tracking."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

        output = await service.track_changes(
            publisher="DU",
            date_from="2024-01-01",
            date_to="2024-12-31",
        )

        assert len(output.changes) == 3
        assert output.date_range == "2024-01-01 to 2024-12-31"
        assert output.result_set_id == "rs_1"
        assert len(output.changes) == output.page_info.returned_count
        assert output.total_count == output.page_info.total_count
        assert output.page_info.unit == PageUnit.ITEMS

    @respx.mock
    async def test_track_changes_stores_the_fetched_window_and_reports_the_corpus(
        self, service: ChangesService, search_results: dict
    ):
        """Issue #54: the store holds what was fetched, `total_count` says how much exists.

        Before the fix the tool fetched an unbounded range, stored it whole and sliced
        locally, so `total_count` was the download size and never the corpus size.
        """
        respx.get(SEARCH_URL).mock(side_effect=_windowed(search_results["items"]))

        output = await service.track_changes(
            publisher="DU",
            date_from="2024-01-01",
            date_to="2024-12-31",
            limit=1,
            offset=0,
        )

        assert output.total_count == 3
        assert len(output.changes) == 1
        assert output.page_info.returned_count == 1
        assert output.page_info.total_count == 3
        assert output.page_info.was_truncated is True
        assert output.page_info.next_offset == 1

        assert output.result_set_id is not None
        stored = await service._result_store.get(output.result_set_id)
        assert stored is not None
        assert len(stored.results) == 1
        assert stored.total_count == 3

    @respx.mock
    async def test_track_changes_defaults_date_to_today(self, service: ChangesService, search_results: dict):
        """Test that date_to defaults to today when not provided."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

        output = await service.track_changes(
            publisher="DU",
            date_from="2024-01-01",
        )

        assert output.date_range.startswith("2024-01-01 to ")
        assert len(output.changes) == 3

    @respx.mock
    async def test_track_changes_with_keywords(self, service: ChangesService, search_results: dict):
        """Test changes tracking with keyword filter."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

        output = await service.track_changes(
            publisher="DU",
            date_from="2024-01-01",
            date_to="2024-12-31",
            keywords=["test", "prawo"],
        )

        assert len(output.changes) > 0

    @respx.mock
    async def test_track_changes_mp_publisher(self, service: ChangesService, search_results: dict):
        """Test changes tracking for Monitor Polski."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

        output = await service.track_changes(
            publisher="MP",
            date_from="2024-01-01",
            date_to="2024-12-31",
        )

        assert len(output.changes) > 0

    @respx.mock
    async def test_track_changes_empty_results(self, service: ChangesService):
        """Test changes tracking with no results."""
        empty_results = {"count": 0, "items": []}
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=empty_results))

        output = await service.track_changes(
            publisher="DU",
            date_from="1900-01-01",
            date_to="1900-12-31",
        )

        assert len(output.changes) == 0
        assert "1900-01-01 to 1900-12-31" in output.date_range
        assert output.result_set_id is None

    @respx.mock
    async def test_track_changes_result_formatting(self, service: ChangesService, search_results: dict):
        """Test that results are properly formatted."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

        output = await service.track_changes(
            publisher="DU",
            date_from="2024-01-01",
            date_to="2024-12-31",
        )

        assert output.changes[0].eli == "DU/2024/1"
        assert output.changes[0].publisher == "DU"
        assert output.changes[0].year == 2024
        assert output.changes[0].pos == 1
        assert output.changes[0].title == "Ustawa testowa 1"
        assert output.changes[0].status == "akt obowiązujący"
        assert output.changes[0].type == "Ustawa"
        assert output.changes[0].promulgation_date == "2024-01-05"
        assert output.changes[0].effective_date == "2024-02-01"
        assert output.changes[0].in_force == "YES"

    @respx.mock
    async def test_track_changes_multiple_keywords(self, service: ChangesService, search_results: dict):
        """Test changes tracking with multiple keywords."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

        output = await service.track_changes(
            publisher="DU",
            date_from="2024-01-01",
            date_to="2024-12-31",
            keywords=["ustawa", "kodeks", "prawo"],
        )

        assert len(output.changes) > 0

    @respx.mock
    async def test_track_changes_api_error(self, service: ChangesService):
        """Test handling of API errors."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(500))

        with pytest.raises(Exception):  # noqa: B017
            await service.track_changes(
                publisher="DU",
                date_from="2024-01-01",
            )

    @respx.mock
    async def test_track_changes_handles_missing_fields(self, service: ChangesService):
        """Test that missing fields in results are handled gracefully."""
        partial_results = {
            "count": 1,
            "items": [
                {
                    "ELI": "DU/2024/1",
                    "publisher": "DU",
                    "year": 2024,
                    "pos": 1,
                    "title": "Partial Act",
                    "status": "akt obowiązujący",
                }
            ],
        }
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=partial_results))

        output = await service.track_changes(
            publisher="DU",
            date_from="2024-01-01",
        )

        assert len(output.changes) == 1
        assert output.changes[0].eli == "DU/2024/1"
        assert output.changes[0].type is None
        assert output.changes[0].promulgation_date is None

    @respx.mock
    async def test_the_requested_window_reaches_the_api(self, service: ChangesService):
        """Issue #54: `limit` and `offset` are query parameters, not a local slice.

        Without them the request downloads whatever page the API feels like building
        (500 records by default, tests/fixtures/search_default_page.provenance.md)
        to return twenty, and a caller can never reach record 501.
        """
        route = respx.get(SEARCH_URL).mock(side_effect=_windowed([_item(n) for n in range(100)]))

        await service.track_changes(publisher="DU", date_from="2024-01-01", date_to="2024-12-31", limit=20, offset=40)

        params = route.calls.last.request.url.params
        assert params["limit"] == "20"
        assert params["offset"] == "40"

    @respx.mock
    async def test_the_first_page_omits_offset(self, service: ChangesService):
        """Same params dict shape as `search()`: `offset=0` is not sent."""
        route = respx.get(SEARCH_URL).mock(side_effect=_windowed([_item(n) for n in range(5)]))

        await service.track_changes(publisher="DU", date_from="2024-01-01", date_to="2024-12-31")

        params = route.calls.last.request.url.params
        assert params["limit"] == "20"
        assert "offset" not in params

    @respx.mock
    async def test_a_windowed_range_declares_itself_a_page(self, service: ChangesService):
        """Issue #54: twenty records out of five hundred are a window, not the answer."""
        respx.get(SEARCH_URL).mock(side_effect=_windowed([_item(n) for n in range(500)]))

        output = await service.track_changes(publisher="DU", date_from="2024-01-01", date_to="2024-12-31", limit=20)

        assert output.total_count == 500
        assert len(output.changes) == 20
        assert output.result_set_scope is not None
        assert output.result_set_scope.scope == "page"
        assert output.result_set_scope.stored_count == 20
        assert output.result_set_scope.window_offset == 0
        assert output.result_set_scope.corpus_count == 500

    @respx.mock
    async def test_an_empty_filter_on_a_window_is_inconclusive(self, service: ChangesService):
        """A window that was called COMPLETE let filter_results prove a negative it could not."""
        respx.get(SEARCH_URL).mock(side_effect=_windowed([_item(n) for n in range(500)]))

        output = await service.track_changes(publisher="DU", date_from="2024-01-01", date_to="2024-12-31", limit=20)
        assert output.result_set_id is not None
        filtered = await service._result_store.filter_and_store(output.result_set_id, pattern="nie-ma-takiego-slowa")

        assert filtered.filtered_count == 0
        assert filtered.no_match_is_inconclusive is True

    @respx.mock
    async def test_a_range_that_fits_one_page_is_still_complete(self, service: ChangesService, search_results: dict):
        """Criterion 4 (D6) survives paging: a first page holding the whole range is complete.

        `store()` derives the reach from `window_offset == 0` and `len(results) ==
        total_count`; the paging change only makes both inputs truthful.
        """
        respx.get(SEARCH_URL).mock(side_effect=_windowed(search_results["items"]))

        output = await service.track_changes(
            publisher="DU",
            date_from="2024-01-01",
            date_to="2024-12-31",
        )

        assert output.result_set_id is not None
        assert output.result_set_scope is not None
        assert output.result_set_scope.scope == "complete"
        assert output.result_set_scope.window_offset == 0
        assert output.result_set_scope.corpus_count == output.total_count == 3

    @respx.mock
    async def test_a_zero_limit_page_still_reports_the_range_size(self, service: ChangesService):
        """`limit=0` asks upstream for one record and slices it away, as search and browse do."""
        route = respx.get(SEARCH_URL).mock(side_effect=_windowed([_item(n) for n in range(50)]))

        output = await service.track_changes(publisher="DU", date_from="2024-01-01", date_to="2024-12-31", limit=0)

        assert route.calls.last.request.url.params["limit"] == "1"
        assert output.changes == []
        assert output.page_info.limit == 0
        assert output.page_info.total_count == 50
        assert output.result_set_id is None
