"""Tests for ChangesService."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.services.changes_service import ChangesService


class TestChangesService:
    """Tests for changes tracking service."""

    @pytest.fixture
    async def service(self, mock_client: SejmApiClient) -> ChangesService:
        """Create ChangesService instance."""
        return ChangesService(client=mock_client)

    @respx.mock
    async def test_track_changes_basic(
        self, service: ChangesService, search_results: dict
    ):
        """Test basic changes tracking."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(200, json=search_results)
        )

        results, date_range = await service.track_changes(
            publisher="DU",
            date_from="2024-01-01",
            date_to="2024-12-31",
        )

        assert len(results) == 3
        assert date_range == "2024-01-01 to 2024-12-31"

    @respx.mock
    async def test_track_changes_defaults_date_to_today(
        self, service: ChangesService, search_results: dict
    ):
        """Test that date_to defaults to today when not provided."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(200, json=search_results)
        )

        results, date_range = await service.track_changes(
            publisher="DU",
            date_from="2024-01-01",
        )

        # date_to should default to today's date
        assert date_range.startswith("2024-01-01 to ")
        assert len(results) == 3

    @respx.mock
    async def test_track_changes_with_keywords(
        self, service: ChangesService, search_results: dict
    ):
        """Test changes tracking with keyword filter."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(200, json=search_results)
        )

        results, date_range = await service.track_changes(
            publisher="DU",
            date_from="2024-01-01",
            date_to="2024-12-31",
            keywords=["test", "prawo"],
        )

        assert len(results) > 0

    @respx.mock
    async def test_track_changes_mp_publisher(
        self, service: ChangesService, search_results: dict
    ):
        """Test changes tracking for Monitor Polski."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(200, json=search_results)
        )

        results, date_range = await service.track_changes(
            publisher="MP",
            date_from="2024-01-01",
            date_to="2024-12-31",
        )

        assert len(results) > 0

    @respx.mock
    async def test_track_changes_empty_results(self, service: ChangesService):
        """Test changes tracking with no results."""
        empty_results = {"count": 0, "items": []}
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(200, json=empty_results)
        )

        results, date_range = await service.track_changes(
            publisher="DU",
            date_from="1900-01-01",
            date_to="1900-12-31",
        )

        assert len(results) == 0
        assert "1900-01-01 to 1900-12-31" in date_range

    @respx.mock
    async def test_track_changes_result_formatting(
        self, service: ChangesService, search_results: dict
    ):
        """Test that results are properly formatted."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(200, json=search_results)
        )

        results, date_range = await service.track_changes(
            publisher="DU",
            date_from="2024-01-01",
            date_to="2024-12-31",
        )

        # Check first result
        assert results[0].eli == "DU/2024/1"
        assert results[0].publisher == "DU"
        assert results[0].year == 2024
        assert results[0].pos == 1
        assert results[0].title == "Ustawa testowa 1"
        assert results[0].status == "akt obowiązujący"
        assert results[0].type == "Ustawa"
        assert results[0].promulgation_date == "2024-01-05"
        assert results[0].effective_date == "2024-02-01"
        assert results[0].in_force == "YES"

    @respx.mock
    async def test_track_changes_multiple_keywords(
        self, service: ChangesService, search_results: dict
    ):
        """Test changes tracking with multiple keywords."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(200, json=search_results)
        )

        results, date_range = await service.track_changes(
            publisher="DU",
            date_from="2024-01-01",
            date_to="2024-12-31",
            keywords=["ustawa", "kodeks", "prawo"],
        )

        assert len(results) > 0

    @respx.mock
    async def test_track_changes_api_error(self, service: ChangesService):
        """Test handling of API errors."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(500)
        )

        with pytest.raises(Exception):  # noqa: B017
            await service.track_changes(
                publisher="DU",
                date_from="2024-01-01",
            )

    @respx.mock
    async def test_track_changes_handles_missing_fields(
        self, service: ChangesService
    ):
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
                    # Missing optional fields
                }
            ],
        }
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(200, json=partial_results)
        )

        results, date_range = await service.track_changes(
            publisher="DU",
            date_from="2024-01-01",
        )

        assert len(results) == 1
        assert results[0].eli == "DU/2024/1"
        assert results[0].type is None
        assert results[0].promulgation_date is None
