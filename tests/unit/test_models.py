"""Tests for Pydantic models and utility functions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from law_scrapper_mcp.models.api_responses import ActSummary
from law_scrapper_mcp.models.enums import (
    ContentFormat,
    DetailLevel,
    MetadataCategory,
    Publisher,
    RelationshipType,
)
from law_scrapper_mcp.models.pagination import DEFAULT_ITEM_LIMIT, PageInfo, PageUnit
from law_scrapper_mcp.models.tool_inputs import ActDetailsRequest, SearchRequest, parse_eli
from law_scrapper_mcp.models.tool_outputs import (
    ActSummaryOutput,
    ChangesOutput,
    ContentOutput,
    EnrichedResponse,
    FilterOutput,
    Hint,
    MetadataOutput,
    ResultSetScope,
    SearchInActOutput,
    SearchOutput,
    SetScope,
)
from law_scrapper_mcp.services.pagination import paginate_items


class TestParseEli:
    """Tests for parse_eli function."""

    def test_valid_eli_simple(self):
        """Test parsing a valid ELI string."""
        publisher, year, pos = parse_eli("DU/2024/1")
        assert publisher == "DU"
        assert year == 2024
        assert pos == 1

    def test_valid_eli_multi_digit(self):
        """Test parsing ELI with multi-digit year and position."""
        publisher, year, pos = parse_eli("MP/2023/1234")
        assert publisher == "MP"
        assert year == 2023
        assert pos == 1234

    def test_valid_eli_with_url_prefix(self):
        """Test parsing ELI from full API URL."""
        publisher, year, pos = parse_eli("https://api.sejm.gov.pl/eli/DU/2024/1")
        assert publisher == "DU"
        assert year == 2024
        assert pos == 1

    def test_valid_eli_with_trailing_slash(self):
        """Test parsing ELI with trailing slashes."""
        publisher, year, pos = parse_eli("DU/2024/1/")
        assert publisher == "DU"
        assert year == 2024
        assert pos == 1

    @pytest.mark.parametrize(
        "invalid_eli",
        [
            "invalid",
            "DU/2024",
            "DU/abc/1",
            "DU/2024/xyz",
            "DU/2024/1/extra",
            "",
        ],
    )
    def test_invalid_eli_format(self, invalid_eli: str):
        """Test that invalid ELI formats raise ValueError."""
        with pytest.raises(ValueError, match="Nieprawidłowy"):
            parse_eli(invalid_eli)

    def test_invalid_url_format(self):
        """Test that invalid URL format raises ValueError."""
        with pytest.raises(ValueError, match="Nieprawidłowy format URL ELI"):
            parse_eli("http://example.com/DU/2024/1")


class TestEnums:
    """Tests for enum types."""

    def test_publisher_values(self):
        """Test Publisher enum values."""
        assert Publisher.DU.value == "DU"
        assert Publisher.MP.value == "MP"

    def test_publisher_labels(self):
        """Test Publisher enum labels."""
        assert Publisher.DU.label == "Dziennik Ustaw"
        assert Publisher.MP.label == "Monitor Polski"

    def test_detail_level_values(self):
        """Test DetailLevel enum values."""
        assert DetailLevel.MINIMAL.value == "minimal"
        assert DetailLevel.STANDARD.value == "standard"
        assert DetailLevel.FULL.value == "full"

    def test_metadata_category_values(self):
        """Test MetadataCategory enum values."""
        assert MetadataCategory.KEYWORDS.value == "keywords"
        assert MetadataCategory.PUBLISHERS.value == "publishers"
        assert MetadataCategory.ALL.value == "all"

    def test_content_format_values(self):
        """Test ContentFormat enum values."""
        assert ContentFormat.HTML.value == "html"
        assert ContentFormat.PDF.value == "pdf"

    def test_relationship_type_values(self):
        """Test RelationshipType enum values."""
        assert RelationshipType.CHANGED_ACTS.value == "Akty zmienione"
        assert RelationshipType.LEGAL_BASIS.value == "Podstawa prawna"


class TestToolInputModels:
    """Tests for tool input Pydantic models."""

    def test_search_request_defaults(self):
        """Test SearchRequest default values."""
        req = SearchRequest()
        assert req.publisher == Publisher.DU
        assert req.year is None
        assert req.keywords is None
        assert req.detail_level == DetailLevel.STANDARD

    def test_search_request_with_values(self):
        """Test SearchRequest with custom values."""
        req = SearchRequest(
            publisher=Publisher.MP,
            year=2024,
            keywords=["test", "prawo"],
            limit=50,
            detail_level=DetailLevel.FULL,
        )
        assert req.publisher == Publisher.MP
        assert req.year == 2024
        assert req.keywords == ["test", "prawo"]
        assert req.limit == 50
        assert req.detail_level == DetailLevel.FULL

    def test_act_details_request(self):
        """Test ActDetailsRequest model."""
        req = ActDetailsRequest(eli="DU/2024/1", load_content=True)
        assert req.eli == "DU/2024/1"
        assert req.load_content is True

    def test_act_details_request_default(self):
        """Test ActDetailsRequest default load_content."""
        req = ActDetailsRequest(eli="DU/2024/1")
        assert req.load_content is False


class TestToolOutputModels:
    """Tests for tool output Pydantic models."""

    def test_hint_creation(self):
        """Test Hint model creation."""
        hint = Hint(message="Next step", tool="search", parameters={"year": 2024})
        assert hint.message == "Next step"
        assert hint.tool == "search"
        assert hint.parameters == {"year": 2024}

    def test_hint_optional_fields(self):
        """Test Hint with optional fields."""
        hint = Hint(message="Simple hint")
        assert hint.message == "Simple hint"
        assert hint.tool is None
        assert hint.parameters is None

    def test_enriched_response_creation(self):
        """Test EnrichedResponse model creation."""
        data = {"test": "value"}
        hint = Hint(message="Test hint")
        response = EnrichedResponse(data=data, hints=[hint], metadata={"count": 1})
        assert response.data == data
        assert len(response.hints) == 1
        assert response.hints[0].message == "Test hint"
        assert response.metadata == {"count": 1}

    def test_enriched_response_serialization(self):
        """Test EnrichedResponse serialization to dict."""
        data = {"test": "value"}
        response = EnrichedResponse(data=data, hints=[], metadata={"count": 1})
        serialized = response.model_dump()
        assert "data" in serialized
        assert "hints" in serialized
        assert "metadata" in serialized
        assert serialized["data"] == data

    def test_act_summary_output(self):
        """Test ActSummaryOutput model."""
        summary = ActSummaryOutput(
            eli="DU/2024/1",
            publisher="DU",
            year=2024,
            pos=1,
            title="Test Act",
            status="akt obowiązujący",
            type="Ustawa",
            in_force="YES",
        )
        assert summary.eli == "DU/2024/1"
        assert summary.title == "Test Act"
        assert summary.in_force == "YES"

    def test_search_output(self):
        """Test SearchOutput model."""
        summary = ActSummaryOutput(
            eli="DU/2024/1",
            publisher="DU",
            year=2024,
            pos=1,
            title="Test Act",
            status="akt obowiązujący",
        )
        output = SearchOutput(
            results=[summary],
            total_count=1,
            query_summary="Test query",
            returned_count=1,
            page_info=PageInfo(
                limit=20,
                offset=0,
                returned_count=1,
                total_count=1,
                was_truncated=False,
                next_offset=None,
                unit=PageUnit.ITEMS,
            ),
        )
        assert len(output.results) == 1
        assert output.total_count == 1
        assert output.returned_count == 1


class TestPaginationModels:
    """Tests for shared pagination models and paginated outputs."""

    def test_page_info_serializes_unit_enum(self) -> None:
        _, info = paginate_items([1, 2, 3], limit=2, offset=0)

        serialized = info.model_dump()

        assert serialized["unit"] == PageUnit.ITEMS
        assert serialized["returned_count"] == 2
        assert serialized["total_count"] == 3

    def test_paginated_outputs_expose_page_info(self) -> None:
        _, page_info = paginate_items([], limit=DEFAULT_ITEM_LIMIT, offset=0)

        assert (
            ContentOutput(
                eli="DU/2024/1",
                section_title="Spis treści",
                content="",
                page_info=page_info,
            ).page_info.unit
            == PageUnit.ITEMS
        )
        assert (
            SearchInActOutput(
                eli="DU/2024/1",
                query="test",
                matches=[],
                total_matches=0,
                page_info=page_info,
            ).page_info
            == page_info
        )
        assert (
            MetadataOutput(
                category="all",
                metadata={},
                count=0,
                page_info=page_info,
            ).page_info
            == page_info
        )
        assert (
            ChangesOutput(
                date_range="2024-01-01 to 2024-12-31",
                publisher="DU",
                keywords=[],
                changes=[],
                total_count=0,
                page_info=page_info,
            ).page_info
            == page_info
        )
        assert (
            FilterOutput(
                source_result_set_id="rs_1",
                results=[],
                original_count=0,
                filtered_count=0,
                source_scope=ResultSetScope(
                    scope=SetScope.COMPLETE,
                    stored_count=0,
                    window_offset=0,
                    corpus_count=0,
                ),
                page_info=page_info,
            ).page_info
            == page_info
        )

    def test_paginated_outputs_require_page_info(self) -> None:
        with pytest.raises(ValidationError):
            ContentOutput.model_validate(
                {
                    "eli": "DU/2024/1",
                    "section_title": "Spis treści",
                    "content": "",
                }
            )

    def test_models_package_exports_page_types(self) -> None:
        from law_scrapper_mcp.models import PageInfo as ExportedPageInfo
        from law_scrapper_mcp.models import PageUnit as ExportedPageUnit

        assert ExportedPageInfo is PageInfo
        assert ExportedPageUnit is PageUnit
        assert ExportedPageUnit.ITEMS == "items"


class TestApiResponseModels:
    """Tests for API response models."""

    def test_act_summary_from_api_response(self, search_results: dict):
        """Test ActSummary model parsing from API response."""
        item = search_results["items"][0]
        # ActSummary uses field aliases to match API response
        summary = ActSummary(
            ELI=item["ELI"],
            publisher=item["publisher"],
            year=item["year"],
            pos=item["pos"],
            title=item["title"],
            status=item["status"],
            type=item.get("type"),
            promulgation=item.get("promulgation"),
            dateEffect=item.get("dateEffect"),
            inForce=item.get("inForce"),
        )
        assert summary.ELI == "DU/2024/1"
        assert summary.title == "Ustawa testowa 1"
        assert summary.status == "akt obowiązujący"

    def test_pydantic_validation_error(self):
        """Test that invalid data raises ValidationError."""
        with pytest.raises(ValidationError):
            ActSummaryOutput(
                eli="DU/2024/1",
                publisher="DU",
                year="invalid",  # type: ignore[arg-type]  # Should be int
                pos=1,
                title="Test",
                status="test",
            )
