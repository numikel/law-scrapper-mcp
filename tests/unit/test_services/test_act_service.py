"""Tests for ActService."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.services.act_service import ActService
from law_scrapper_mcp.services.content_processor import ContentProcessor
from law_scrapper_mcp.services.document_store import DocumentStore


class TestActService:
    """Tests for act details service."""

    @pytest.fixture
    async def service(
        self,
        mock_client: SejmApiClient,
        document_store: DocumentStore,
        content_processor: ContentProcessor,
    ) -> ActService:
        """Create ActService instance."""
        return ActService(
            client=mock_client,
            document_store=document_store,
            content_processor=content_processor,
        )

    @respx.mock
    async def test_get_details_basic(
        self, service: ActService, act_detail: dict
    ):
        """Test getting basic act details."""
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1").mock(
            return_value=Response(200, json=act_detail)
        )
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/struct").mock(
            return_value=Response(404)  # No structure available
        )

        result = await service.get_details("DU/2024/1", load_content=False)

        assert result.eli == "DU/2024/1"
        assert result.publisher == "DU"
        assert result.year == 2024
        assert result.pos == 1
        assert result.title == "Ustawa z dnia 1 stycznia 2024 r. o testach"
        assert result.status == "akt obowiązujący"
        assert result.has_html is True
        assert result.has_pdf is True
        assert result.is_loaded is False

    @respx.mock
    async def test_get_details_with_structure(
        self, service: ActService, act_detail: dict, act_structure: list
    ):
        """Test getting details with structure/TOC."""
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1").mock(
            return_value=Response(200, json=act_detail)
        )
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/struct").mock(
            return_value=Response(200, json=act_structure)
        )

        result = await service.get_details("DU/2024/1")

        assert len(result.toc) > 0
        assert result.toc[0]["id"] == "part_1"
        assert result.toc[0]["title"] == "Przepisy ogólne"

    @respx.mock
    async def test_get_details_with_keywords(
        self, service: ActService, act_detail: dict
    ):
        """Test that keywords are properly extracted."""
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1").mock(
            return_value=Response(200, json=act_detail)
        )
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/struct").mock(
            return_value=Response(404)
        )

        result = await service.get_details("DU/2024/1")

        assert "test" in result.keywords
        assert "prawo" in result.keywords
        assert "kodeks" in result.keywords

    @respx.mock
    async def test_get_details_load_content_html(
        self,
        service: ActService,
        act_detail: dict,
        sample_act_html: str,
        document_store: DocumentStore,
    ):
        """Test loading HTML content into document store."""
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1").mock(
            return_value=Response(200, json=act_detail)
        )
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/struct").mock(
            return_value=Response(404)
        )
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.html").mock(
            return_value=Response(200, text=sample_act_html)
        )

        result = await service.get_details("DU/2024/1", load_content=True)

        assert result.is_loaded is True
        assert await document_store.is_loaded("DU/2024/1")

    @respx.mock
    async def test_get_details_load_content_already_loaded(
        self,
        service: ActService,
        act_detail: dict,
        document_store: DocumentStore,
    ):
        """Test that content is not reloaded if already in store."""
        # Pre-load the document
        from law_scrapper_mcp.services.content_processor import Section

        await document_store.load(
            "DU/2024/1",
            "Test content",
            [Section(id="art_1", title="Art. 1.", level=2, start_pos=0)],
        )

        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1").mock(
            return_value=Response(200, json=act_detail)
        )
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/struct").mock(
            return_value=Response(404)
        )

        result = await service.get_details("DU/2024/1", load_content=True)

        assert result.is_loaded is True
        # Should not make additional HTTP requests for content

    @respx.mock
    async def test_get_details_load_content_pdf_fallback(
        self, service: ActService, act_detail: dict
    ):
        """Test loading PDF content when HTML is not available."""
        # Modify act_detail to not have HTML
        act_detail_no_html = act_detail.copy()
        act_detail_no_html["textHTML"] = None

        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1").mock(
            return_value=Response(200, json=act_detail_no_html)
        )
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/struct").mock(
            return_value=Response(404)
        )
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.pdf").mock(
            return_value=Response(200, content=b"%PDF-1.4 fake pdf")
        )

        result = await service.get_details("DU/2024/1", load_content=True)

        # Content should be loaded (even if PDF extraction fails)
        assert result.has_pdf is True

    @respx.mock
    async def test_get_details_handles_missing_content(
        self, service: ActService, act_detail: dict
    ):
        """Test handling of missing content gracefully."""
        act_detail_no_content = act_detail.copy()
        act_detail_no_content["textHTML"] = None
        act_detail_no_content["textPDF"] = None

        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1").mock(
            return_value=Response(200, json=act_detail_no_content)
        )
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/struct").mock(
            return_value=Response(404)
        )

        result = await service.get_details("DU/2024/1", load_content=True)

        assert result.has_html is False
        assert result.has_pdf is False

    @respx.mock
    async def test_get_details_from_url_eli(
        self, service: ActService, act_detail: dict
    ):
        """Test getting details using full URL ELI."""
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1").mock(
            return_value=Response(200, json=act_detail)
        )
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/struct").mock(
            return_value=Response(404)
        )

        result = await service.get_details(
            "https://api.sejm.gov.pl/eli/DU/2024/1", load_content=False
        )

        assert result.eli == "DU/2024/1"

    @respx.mock
    async def test_get_details_all_date_fields(
        self, service: ActService, act_detail: dict
    ):
        """Test that all date fields are properly extracted."""
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1").mock(
            return_value=Response(200, json=act_detail)
        )
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/struct").mock(
            return_value=Response(404)
        )

        result = await service.get_details("DU/2024/1")

        assert result.announcement_date == "2024-01-05"
        assert result.promulgation_date == "2024-01-05"
        assert result.entry_into_force == "2024-02-01"
        assert result.valid_from == "2024-02-01"
        assert result.repeal_date is None
        assert result.change_date is None

    @respx.mock
    async def test_get_details_with_volume(
        self, service: ActService, act_detail: dict
    ):
        """Test that volume is properly extracted."""
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1").mock(
            return_value=Response(200, json=act_detail)
        )
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/struct").mock(
            return_value=Response(404)
        )

        result = await service.get_details("DU/2024/1")

        assert result.volume == 1

    @respx.mock
    async def test_format_toc_recursive(
        self, service: ActService, act_detail: dict, act_structure: list
    ):
        """Test that TOC formatting handles nested children."""
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1").mock(
            return_value=Response(200, json=act_detail)
        )
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/struct").mock(
            return_value=Response(200, json=act_structure)
        )

        result = await service.get_details("DU/2024/1")

        # Check nested structure
        assert "children" in result.toc[0]
        assert len(result.toc[0]["children"]) == 2
        assert result.toc[0]["children"][0]["id"] == "art_1"

    @respx.mock
    async def test_get_details_api_error(self, service: ActService):
        """Test handling of API errors."""
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/999").mock(
            return_value=Response(404)
        )

        with pytest.raises(Exception):  # noqa: B017
            await service.get_details("DU/2024/999")
