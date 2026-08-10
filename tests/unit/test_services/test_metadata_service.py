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
    async def test_get_metadata_keywords(self, service: MetadataService):
        """Test fetching keywords metadata."""
        mock_keywords = ["prawo", "ustawa", "kodeks"]
        respx.get("https://api.sejm.gov.pl/eli/keywords").mock(return_value=Response(200, json=mock_keywords))

        result = await service.get_metadata(MetadataCategory.KEYWORDS)

        assert "keywords" in result
        assert result["keywords"] == mock_keywords

    @respx.mock
    async def test_get_metadata_publishers(self, service: MetadataService, publishers_data: list):
        """Test fetching publishers metadata."""
        respx.get("https://api.sejm.gov.pl/eli/acts").mock(return_value=Response(200, json=publishers_data))

        result = await service.get_metadata(MetadataCategory.PUBLISHERS)

        assert "publishers" in result
        assert len(result["publishers"]) == 2
        assert result["publishers"][0]["code"] == "DU"

    @respx.mock
    async def test_get_metadata_statuses(self, service: MetadataService):
        """Test fetching statuses metadata."""
        mock_statuses = ["akt obowiązujący", "uchylony", "nieobowiązujący"]
        respx.get("https://api.sejm.gov.pl/eli/statuses").mock(return_value=Response(200, json=mock_statuses))

        result = await service.get_metadata(MetadataCategory.STATUSES)

        assert "statuses" in result
        assert "akt obowiązujący" in result["statuses"]

    @respx.mock
    async def test_get_metadata_types(self, service: MetadataService):
        """Test fetching types metadata."""
        mock_types = ["Ustawa", "Rozporządzenie", "Obwieszczenie"]
        respx.get("https://api.sejm.gov.pl/eli/types").mock(return_value=Response(200, json=mock_types))

        result = await service.get_metadata(MetadataCategory.TYPES)

        assert "types" in result
        assert "Ustawa" in result["types"]

    @respx.mock
    async def test_get_metadata_institutions(self, service: MetadataService):
        """Test fetching institutions metadata."""
        mock_institutions = ["Sejm RP", "Senat RP", "Prezydent RP"]
        respx.get("https://api.sejm.gov.pl/eli/institutions").mock(return_value=Response(200, json=mock_institutions))

        result = await service.get_metadata(MetadataCategory.INSTITUTIONS)

        assert "institutions" in result
        assert "Sejm RP" in result["institutions"]

    @respx.mock
    async def test_get_metadata_all(self, service: MetadataService):
        """Test fetching all metadata categories."""
        respx.get("https://api.sejm.gov.pl/eli/keywords").mock(return_value=Response(200, json=["prawo"]))
        respx.get("https://api.sejm.gov.pl/eli/acts").mock(return_value=Response(200, json=[]))
        respx.get("https://api.sejm.gov.pl/eli/statuses").mock(return_value=Response(200, json=["akt obowiązujący"]))
        respx.get("https://api.sejm.gov.pl/eli/types").mock(return_value=Response(200, json=["Ustawa"]))
        respx.get("https://api.sejm.gov.pl/eli/institutions").mock(return_value=Response(200, json=["Sejm RP"]))

        result = await service.get_metadata(MetadataCategory.ALL)

        assert "keywords" in result
        assert "publishers" in result
        assert "statuses" in result
        assert "types" in result
        assert "institutions" in result

    @respx.mock
    async def test_get_metadata_all_handles_errors(self, service: MetadataService):
        """Test that ALL category handles errors gracefully."""
        respx.get("https://api.sejm.gov.pl/eli/keywords").mock(return_value=Response(200, json=["prawo"]))
        respx.get("https://api.sejm.gov.pl/eli/acts").mock(
            return_value=Response(500)  # Simulate error
        )
        respx.get("https://api.sejm.gov.pl/eli/statuses").mock(return_value=Response(200, json=["akt obowiązujący"]))
        respx.get("https://api.sejm.gov.pl/eli/types").mock(return_value=Response(200, json=["Ustawa"]))
        respx.get("https://api.sejm.gov.pl/eli/institutions").mock(return_value=Response(200, json=["Sejm RP"]))

        result = await service.get_metadata(MetadataCategory.ALL)

        # Should still return results for successful categories
        assert "keywords" in result
        assert "publishers" in result  # Should be empty list on error
        assert result["publishers"] == []

    @respx.mock
    async def test_get_metadata_api_error(self, service: MetadataService):
        """Test handling of API errors."""
        respx.get("https://api.sejm.gov.pl/eli/keywords").mock(return_value=Response(500))

        with pytest.raises(Exception):  # noqa: B017
            await service.get_metadata(MetadataCategory.KEYWORDS)

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
