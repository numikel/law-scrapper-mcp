"""Tests for SearchService."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.models.enums import DetailLevel
from law_scrapper_mcp.services.result_store import ResultStore
from law_scrapper_mcp.services.search_service import SearchService

pytestmark = pytest.mark.asyncio


class TestSearchService:
    """Tests for search service."""

    @pytest.fixture
    def service(self, mock_client: SejmApiClient) -> SearchService:
        """Create SearchService instance."""
        return SearchService(client=mock_client, result_store=ResultStore())

    @respx.mock
    async def test_search_basic(self, service: SearchService, search_results: dict):
        """Test basic search."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

        output = await service.search(publisher="DU", year=2024)

        assert len(output.results) == 3
        assert output.total_count == 3
        assert "publisher=DU" in output.query_summary
        assert "year=2024" in output.query_summary
        assert output.result_set_id == "rs_1"

    @respx.mock
    async def test_search_with_keywords(self, service: SearchService, search_results: dict):
        """Test search with keywords."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

        output = await service.search(publisher="DU", keywords=["test", "prawo"])

        assert len(output.results) > 0
        assert "keywords=test,prawo" in output.query_summary

    @respx.mock
    async def test_search_with_date_range(self, service: SearchService, search_results: dict):
        """Test search with date range."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

        output = await service.search(
            publisher="DU",
            date_from="2024-01-01",
            date_to="2024-12-31",
        )

        assert len(output.results) > 0
        assert "effective_from=2024-01-01" in output.query_summary
        assert "effective_to=2024-12-31" in output.query_summary

    @respx.mock
    async def test_search_with_title(self, service: SearchService, search_results: dict):
        """Test search with title filter."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

        output = await service.search(publisher="DU", title="Ustawa testowa")

        assert "title=Ustawa testowa" in output.query_summary

    @respx.mock
    async def test_search_with_in_force_filter(self, service: SearchService, search_results: dict):
        """Test search with in_force filter."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

        output = await service.search(publisher="DU", in_force=True)

        assert "in_force=True" in output.query_summary

    @respx.mock
    async def test_search_with_pagination(self, service: SearchService, search_results: dict):
        """Test search with limit and offset."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

        output = await service.search(publisher="DU", limit=50, offset=10)

        assert len(output.results) > 0

    @respx.mock
    async def test_search_empty_results(self, service: SearchService):
        """Test search with empty results."""
        empty_results = {"count": 0, "items": []}
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=empty_results))

        output = await service.search(publisher="DU", keywords=["nonexistent"])

        assert len(output.results) == 0
        assert output.total_count == 0
        assert output.result_set_id is None

    @respx.mock
    async def test_search_detail_level_minimal(self, service: SearchService, search_results: dict):
        """Test search with minimal detail level."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

        output = await service.search(publisher="DU", detail_level=DetailLevel.MINIMAL)

        assert output.results[0].eli is not None
        assert output.results[0].title is not None
        assert output.results[0].type is None

    @respx.mock
    async def test_search_detail_level_standard(self, service: SearchService, search_results: dict):
        """Test search with standard detail level."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

        output = await service.search(publisher="DU", detail_level=DetailLevel.STANDARD)

        assert output.results[0].type is not None
        assert output.results[0].promulgation_date is not None

    @respx.mock
    async def test_search_detail_level_full(self, service: SearchService, search_results: dict):
        """Test search with full detail level."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

        output = await service.search(publisher="DU", detail_level=DetailLevel.FULL)

        assert output.results[0].type is not None
        assert output.results[0].in_force is not None

    @respx.mock
    async def test_browse_by_publisher_year(self, service: SearchService, search_results: dict):
        """Test browsing acts by publisher and year."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

        output = await service.browse("DU", 2024)

        assert len(output.results) == 3
        assert output.total_count == 3
        assert output.query_summary == "publisher=DU | year=2024"

    @respx.mock
    async def test_browse_with_detail_level(self, service: SearchService, search_results: dict):
        """Test browse with detail level."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

        output = await service.browse("MP", 2023, detail_level=DetailLevel.FULL)

        assert len(output.results) > 0
        assert output.results[0].type is not None

    @respx.mock
    async def test_browse_empty_results(self, service: SearchService):
        """Test browse with no results."""
        empty_results = {"totalCount": 0, "items": []}
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=empty_results))

        output = await service.browse("DU", 1900)

        assert len(output.results) == 0
        assert output.total_count == 0

    @respx.mock
    async def test_search_formats_query_summary_correctly(self, service: SearchService, search_results: dict):
        """Test that query summary is properly formatted."""
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=search_results))

        output = await service.search(
            publisher="DU",
            year=2024,
            keywords=["test"],
            in_force=True,
        )

        assert "publisher=DU" in output.query_summary
        assert "year=2024" in output.query_summary
        assert "keywords=test" in output.query_summary
        assert "in_force=True" in output.query_summary
        assert " | " in output.query_summary

    @respx.mock
    async def test_search_applies_default_limit(self, service: SearchService):
        """Test that search stores at most 20 results by default."""
        many_results = {
            "count": 25,
            "items": [
                {
                    "ELI": f"DU/2024/{index}",
                    "publisher": "DU",
                    "year": 2024,
                    "pos": index,
                    "title": f"Act {index}",
                    "status": "akt obowiązujący",
                }
                for index in range(1, 26)
            ],
        }
        respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(return_value=Response(200, json=many_results))

        output = await service.search(publisher="DU")

        assert output.returned_count == 20
        assert output.total_count == 25
