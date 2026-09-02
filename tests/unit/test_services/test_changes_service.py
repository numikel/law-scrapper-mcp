"""Tests for ChangesService."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.models.pagination import PageUnit
from law_scrapper_mcp.services.changes_service import ChangesService
from law_scrapper_mcp.services.result_store import ResultStore

pytestmark = pytest.mark.asyncio


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
    async def test_track_changes_stores_full_set_but_pages_response(
        self, service: ChangesService, search_results: dict
    ):
        """Tracked changes store the complete set before paging the response."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

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

        assert output.result_set_id is not None
        stored = await service._result_store.get(output.result_set_id)
        assert stored is not None
        assert len(stored.results) == 3

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
    async def test_tracked_changes_are_a_complete_set(self, service: ChangesService, search_results: dict):
        """Criterion 4 (D6). The one tool whose set filter_results searches whole.

        No behaviour changes here — `ChangesService` already stored the full fetched
        list. The cluster only gives that fact a name in the contract.
        """
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

        output = await service.track_changes(
            publisher="DU",
            date_from="2024-01-01",
            date_to="2024-12-31",
        )

        assert output.result_set_id is not None
        assert output.result_set_scope is not None
        assert output.result_set_scope.scope == "complete"
        assert output.result_set_scope.window_offset == 0
        assert output.result_set_scope.corpus_count == output.total_count
