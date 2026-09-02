"""Tests for MetadataService."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.models.enums import MetadataCategory
from law_scrapper_mcp.models.pagination import MAX_ITEM_LIMIT
from law_scrapper_mcp.services.metadata_service import MetadataService

pytestmark = pytest.mark.asyncio


class TestMetadataService:
    """Tests for metadata service."""

    @pytest.fixture
    def service(self, mock_client: SejmApiClient) -> MetadataService:
        """Create MetadataService instance."""
        return MetadataService(client=mock_client)

    @respx.mock
    async def test_get_metadata_page_keywords(self, service: MetadataService):
        """Test fetching keywords metadata."""
        mock_keywords = ["prawo", "ustawa", "kodeks"]
        respx.get("https://api.sejm.gov.pl/eli/keywords").mock(return_value=Response(200, json=mock_keywords))

        output = await service.get_metadata_page(MetadataCategory.KEYWORDS)

        assert "keywords" in output.metadata
        assert output.metadata["keywords"] == mock_keywords

    @respx.mock
    async def test_get_metadata_page_publishers(self, service: MetadataService, publishers_data: list):
        """Test fetching publishers metadata."""
        respx.get("https://api.sejm.gov.pl/eli/acts").mock(return_value=Response(200, json=publishers_data))

        output = await service.get_metadata_page(MetadataCategory.PUBLISHERS)

        assert "publishers" in output.metadata
        assert len(output.metadata["publishers"]) == 2
        assert output.metadata["publishers"][0]["code"] == "DU"

    @respx.mock
    async def test_get_metadata_page_statuses(self, service: MetadataService):
        """Test fetching statuses metadata."""
        mock_statuses = ["akt obowiązujący", "uchylony", "nieobowiązujący"]
        respx.get("https://api.sejm.gov.pl/eli/statuses").mock(return_value=Response(200, json=mock_statuses))

        output = await service.get_metadata_page(MetadataCategory.STATUSES)

        assert "statuses" in output.metadata
        assert "akt obowiązujący" in output.metadata["statuses"]

    @respx.mock
    async def test_get_metadata_page_types(self, service: MetadataService):
        """Test fetching types metadata."""
        mock_types = ["Ustawa", "Rozporządzenie", "Obwieszczenie"]
        respx.get("https://api.sejm.gov.pl/eli/types").mock(return_value=Response(200, json=mock_types))

        output = await service.get_metadata_page(MetadataCategory.TYPES)

        assert "types" in output.metadata
        assert "Ustawa" in output.metadata["types"]

    @respx.mock
    async def test_get_metadata_page_institutions(self, service: MetadataService):
        """Test fetching institutions metadata."""
        mock_institutions = ["Sejm RP", "Senat RP", "Prezydent RP"]
        respx.get("https://api.sejm.gov.pl/eli/institutions").mock(return_value=Response(200, json=mock_institutions))

        output = await service.get_metadata_page(MetadataCategory.INSTITUTIONS)

        assert "institutions" in output.metadata
        assert "Sejm RP" in output.metadata["institutions"]

    @respx.mock
    async def test_get_metadata_page_all(self, service: MetadataService):
        """Test fetching all metadata categories."""
        respx.get("https://api.sejm.gov.pl/eli/keywords").mock(return_value=Response(200, json=["prawo"]))
        respx.get("https://api.sejm.gov.pl/eli/acts").mock(return_value=Response(200, json=[]))
        respx.get("https://api.sejm.gov.pl/eli/statuses").mock(return_value=Response(200, json=["akt obowiązujący"]))
        respx.get("https://api.sejm.gov.pl/eli/types").mock(return_value=Response(200, json=["Ustawa"]))
        respx.get("https://api.sejm.gov.pl/eli/institutions").mock(return_value=Response(200, json=["Sejm RP"]))

        output = await service.get_metadata_page(MetadataCategory.ALL)

        assert "keywords" in output.metadata
        assert "publishers" in output.metadata
        assert "statuses" in output.metadata
        assert "types" in output.metadata
        assert "institutions" in output.metadata

    @respx.mock
    async def test_get_metadata_page_all_handles_errors(self, service: MetadataService):
        """Test that ALL category handles errors gracefully."""
        respx.get("https://api.sejm.gov.pl/eli/keywords").mock(return_value=Response(200, json=["prawo"]))
        respx.get("https://api.sejm.gov.pl/eli/acts").mock(
            return_value=Response(500)  # Simulate error
        )
        respx.get("https://api.sejm.gov.pl/eli/statuses").mock(return_value=Response(200, json=["akt obowiązujący"]))
        respx.get("https://api.sejm.gov.pl/eli/types").mock(return_value=Response(200, json=["Ustawa"]))
        respx.get("https://api.sejm.gov.pl/eli/institutions").mock(return_value=Response(200, json=["Sejm RP"]))

        output = await service.get_metadata_page(MetadataCategory.ALL)

        # Should still return results for successful categories, and name the
        # failed one so a partial result is never mistaken for a complete one
        assert "keywords" in output.metadata
        assert "publishers" in output.metadata  # Should be empty list on error
        assert output.metadata["publishers"] == []
        assert output.failed_categories == ["publishers"]

    @respx.mock
    async def test_get_metadata_page_api_error(self, service: MetadataService):
        """Test handling of API errors."""
        respx.get("https://api.sejm.gov.pl/eli/keywords").mock(return_value=Response(500))

        with pytest.raises(Exception):  # noqa: B017
            await service.get_metadata_page(MetadataCategory.KEYWORDS)

    @respx.mock
    async def test_get_metadata_page_all_preserves_category_keys(self, service: MetadataService):
        """Global metadata paging keeps all category keys on each page."""
        respx.get("https://api.sejm.gov.pl/eli/keywords").mock(
            return_value=Response(200, json=["prawo", "ustawa", "kodeks"])
        )
        respx.get("https://api.sejm.gov.pl/eli/acts").mock(return_value=Response(200, json=[]))
        respx.get("https://api.sejm.gov.pl/eli/statuses").mock(return_value=Response(200, json=["akt obowiązujący"]))
        respx.get("https://api.sejm.gov.pl/eli/types").mock(return_value=Response(200, json=["Ustawa"]))
        respx.get("https://api.sejm.gov.pl/eli/institutions").mock(return_value=Response(200, json=["Sejm RP"]))

        output = await service.get_metadata_page(MetadataCategory.ALL, limit=2, offset=0)

        assert output.count == 2
        assert list(output.metadata) == [
            "keywords",
            "publishers",
            "statuses",
            "types",
            "institutions",
        ]
        assert sum(len(values) for values in output.metadata.values()) == 2
        assert output.page_info.total_count == 6

    @respx.mock
    async def test_get_metadata_page_clamps_limit_in_service(self, service: MetadataService):
        """Metadata paging clamps oversized limits in the service layer."""
        keywords = [f"keyword-{index}" for index in range(MAX_ITEM_LIMIT + 5)]
        respx.get("https://api.sejm.gov.pl/eli/keywords").mock(return_value=Response(200, json=keywords))

        output = await service.get_metadata_page(MetadataCategory.KEYWORDS, limit=500, offset=0)

        assert output.page_info.limit == MAX_ITEM_LIMIT
        assert output.count == MAX_ITEM_LIMIT
        assert len(output.metadata["keywords"]) == MAX_ITEM_LIMIT

    @respx.mock
    async def test_get_metadata_page_single_category(self, service: MetadataService):
        """Single-category paging slices only that category."""
        respx.get("https://api.sejm.gov.pl/eli/keywords").mock(
            return_value=Response(200, json=["prawo", "ustawa", "kodeks"])
        )

        output = await service.get_metadata_page(MetadataCategory.KEYWORDS, limit=1, offset=1)

        assert output.count == 1
        assert output.metadata == {"keywords": ["ustawa"]}
        assert output.page_info.offset == 1
