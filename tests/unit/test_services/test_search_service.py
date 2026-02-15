"""Tests for SearchService."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.models.enums import DetailLevel
from law_scrapper_mcp.services.search_service import SearchService


class TestSearchService:
    """Tests for search service."""

    @pytest.fixture
    async def service(self, mock_client: SejmApiClient) -> SearchService:
        """Create SearchService instance."""
        return SearchService(client=mock_client)

    @respx.mock
    async def test_search_basic(
        self, service: SearchService, search_results: dict
    ):
        """Test basic search."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(200, json=search_results)
        )

        results, total_count, query_summary = await service.search(
            publisher="DU", year=2024
        )

        assert len(results) == 3
        assert total_count == 3
        assert "publisher=DU" in query_summary
        assert "year=2024" in query_summary

    @respx.mock
    async def test_search_with_keywords(
        self, service: SearchService, search_results: dict
    ):
        """Test search with keywords."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(200, json=search_results)
        )

        results, total_count, query_summary = await service.search(
            publisher="DU", keywords=["test", "prawo"]
        )

        assert len(results) > 0
        assert "keywords=test,prawo" in query_summary

    @respx.mock
    async def test_search_with_date_range(
        self, service: SearchService, search_results: dict
    ):
        """Test search with date range."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(200, json=search_results)
        )

        results, total_count, query_summary = await service.search(
            publisher="DU",
            date_from="2024-01-01",
            date_to="2024-12-31",
        )

        assert len(results) > 0
        assert "effective_from=2024-01-01" in query_summary
        assert "effective_to=2024-12-31" in query_summary

    @respx.mock
    async def test_search_with_title(
        self, service: SearchService, search_results: dict
    ):
        """Test search with title filter."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(200, json=search_results)
        )

        results, total_count, query_summary = await service.search(
            publisher="DU", title="Ustawa testowa"
        )

        assert "title=Ustawa testowa" in query_summary

    @respx.mock
    async def test_search_with_in_force_filter(
        self, service: SearchService, search_results: dict
    ):
        """Test search with in_force filter."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(200, json=search_results)
        )

        results, total_count, query_summary = await service.search(
            publisher="DU", in_force=True
        )

        assert "in_force=True" in query_summary

    @respx.mock
    async def test_search_with_pagination(
        self, service: SearchService, search_results: dict
    ):
        """Test search with limit and offset."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(200, json=search_results)
        )

        results, total_count, query_summary = await service.search(
            publisher="DU", limit=50, offset=10
        )

        assert len(results) > 0

    @respx.mock
    async def test_search_empty_results(self, service: SearchService):
        """Test search with empty results."""
        empty_results = {"count": 0, "items": []}
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(200, json=empty_results)
        )

        results, total_count, query_summary = await service.search(
            publisher="DU", keywords=["nonexistent"]
        )

        assert len(results) == 0
        assert total_count == 0

    @respx.mock
    async def test_search_detail_level_minimal(
        self, service: SearchService, search_results: dict
    ):
        """Test search with minimal detail level."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(200, json=search_results)
        )

        results, _, _ = await service.search(
            publisher="DU", detail_level=DetailLevel.MINIMAL
        )

        # Minimal level should not include optional fields
        assert results[0].eli is not None
        assert results[0].title is not None
        # Type is optional and should not be set in MINIMAL
        assert results[0].type is None

    @respx.mock
    async def test_search_detail_level_standard(
        self, service: SearchService, search_results: dict
    ):
        """Test search with standard detail level."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(200, json=search_results)
        )

        results, _, _ = await service.search(
            publisher="DU", detail_level=DetailLevel.STANDARD
        )

        # Standard level should include type, dates, etc.
        assert results[0].type is not None
        assert results[0].promulgation_date is not None

    @respx.mock
    async def test_search_detail_level_full(
        self, service: SearchService, search_results: dict
    ):
        """Test search with full detail level."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(200, json=search_results)
        )

        results, _, _ = await service.search(
            publisher="DU", detail_level=DetailLevel.FULL
        )

        # Full level should include all available fields
        assert results[0].type is not None
        assert results[0].in_force is not None

    @respx.mock
    async def test_browse_by_publisher_year(
        self, service: SearchService, search_results: dict
    ):
        """Test browsing acts by publisher and year."""
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024").mock(
            return_value=Response(200, json=search_results)
        )

        results, total_count = await service.browse("DU", 2024)

        assert len(results) == 3
        assert total_count == 3

    @respx.mock
    async def test_browse_with_detail_level(
        self, service: SearchService, search_results: dict
    ):
        """Test browse with detail level."""
        respx.get("https://api.sejm.gov.pl/eli/acts/MP/2023").mock(
            return_value=Response(200, json=search_results)
        )

        results, total_count = await service.browse(
            "MP", 2023, detail_level=DetailLevel.FULL
        )

        assert len(results) > 0
        assert results[0].type is not None

    @respx.mock
    async def test_browse_empty_results(self, service: SearchService):
        """Test browse with no results."""
        empty_results = {"totalCount": 0, "items": []}
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/1900").mock(
            return_value=Response(200, json=empty_results)
        )

        results, total_count = await service.browse("DU", 1900)

        assert len(results) == 0
        assert total_count == 0

    @respx.mock
    async def test_search_formats_query_summary_correctly(
        self, service: SearchService, search_results: dict
    ):
        """Test that query summary is properly formatted."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
            return_value=Response(200, json=search_results)
        )

        results, total_count, query_summary = await service.search(
            publisher="DU",
            year=2024,
            keywords=["test"],
            in_force=True,
        )

        # Summary should contain all query parameters
        assert "publisher=DU" in query_summary
        assert "year=2024" in query_summary
        assert "keywords=test" in query_summary
        assert "in_force=True" in query_summary
        # Parameters should be separated by |
        assert " | " in query_summary
