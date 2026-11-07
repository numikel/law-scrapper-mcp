"""The document identifier and kind are additive: a consumer of the old content outputs sees nothing new it must send or read."""

from __future__ import annotations

from typing import Any

from law_scrapper_mcp.models.pagination import PageInfo, PageUnit
from law_scrapper_mcp.models.tool_outputs import (
    ContentOutput,
    DocumentKind,
    EnrichedResponse,
    LoadedDocumentInfo,
    LoadedDocumentListOutput,
    SearchInActOutput,
)

# Written out by hand, as they were before judgments could be read. A new required field on any of
# these outputs would break every client that builds or validates the old shape, so these sets may
# only grow by optional fields.
CONTENT_REQUIRED = {"eli", "section_title", "content", "page_info"}
CONTENT_PREVIOUS = CONTENT_REQUIRED | {"section_id", "toc"}
SEARCH_REQUIRED = {"eli", "query", "matches", "total_matches", "page_info"}
SEARCH_PREVIOUS = SEARCH_REQUIRED | {"context_chars_requested", "context_chars_applied"}
LOADED_REQUIRED = {"eli", "size_bytes", "section_count", "loaded_at", "last_accessed"}
LOADED_PREVIOUS = LOADED_REQUIRED
ADDED = {"document_id", "document_kind"}


def _schema(model: type) -> dict[str, Any]:
    return model.model_json_schema()  # type: ignore[attr-defined, no-any-return]


def _page_info() -> PageInfo:
    return PageInfo(
        limit=20,
        offset=0,
        returned_count=0,
        total_count=0,
        was_truncated=False,
        next_offset=None,
        unit=PageUnit.ITEMS,
    )


class TestRequiredListsAreUnchanged:
    def test_content_output(self) -> None:
        assert set(_schema(ContentOutput)["required"]) == CONTENT_REQUIRED

    def test_search_in_act_output(self) -> None:
        assert set(_schema(SearchInActOutput)["required"]) == SEARCH_REQUIRED

    def test_loaded_document_info(self) -> None:
        assert set(_schema(LoadedDocumentInfo)["required"]) == LOADED_REQUIRED


class TestOnlyTheTwoFieldsWereAdded:
    def test_content_output(self) -> None:
        properties = set(_schema(ContentOutput)["properties"])
        assert properties >= CONTENT_PREVIOUS
        assert properties - CONTENT_PREVIOUS == ADDED

    def test_search_in_act_output(self) -> None:
        properties = set(_schema(SearchInActOutput)["properties"])
        assert properties >= SEARCH_PREVIOUS
        assert properties - SEARCH_PREVIOUS == ADDED

    def test_loaded_document_info(self) -> None:
        properties = set(_schema(LoadedDocumentInfo)["properties"])
        assert properties >= LOADED_PREVIOUS
        assert properties - LOADED_PREVIOUS == ADDED

    def test_the_list_output_keeps_its_shape(self) -> None:
        assert set(_schema(LoadedDocumentListOutput)["properties"]) == {"documents", "count", "page_info"}
        assert set(_schema(LoadedDocumentListOutput)["required"]) == {"documents", "count", "page_info"}

    def test_the_tool_envelope_is_untouched(self) -> None:
        for model in (ContentOutput, SearchInActOutput, LoadedDocumentListOutput):
            schema = _schema(EnrichedResponse[model])  # type: ignore[valid-type]
            assert set(schema["properties"]) == {"data", "hints"}
            assert schema["required"] == ["data"]

    def test_the_new_fields_are_never_required(self) -> None:
        for model in (ContentOutput, SearchInActOutput, LoadedDocumentInfo):
            assert not ADDED & set(_schema(model)["required"]), model.__name__

    def test_the_kind_is_the_shared_enum(self) -> None:
        schema = _schema(ContentOutput)
        refs = str(schema["properties"]["document_kind"])
        assert "DocumentKind" in refs
        assert schema["$defs"]["DocumentKind"]["enum"] == ["act", "judgment"]


class TestTheOldShapeStillBuilds:
    def test_content_output_built_the_old_way(self) -> None:
        output = ContentOutput(eli="DU/2024/1", section_title="Spis treści", content="x", page_info=_page_info())

        assert output.document_id is None
        assert output.document_kind is None
        assert output.model_dump(mode="json")["eli"] == "DU/2024/1"

    def test_search_in_act_output_built_the_old_way(self) -> None:
        output = SearchInActOutput(eli="DU/2024/1", query="q", matches=[], total_matches=0, page_info=_page_info())

        assert output.document_id is None
        assert output.document_kind is None

    def test_loaded_document_info_built_the_old_way(self) -> None:
        info = LoadedDocumentInfo(
            eli="DU/2024/1",
            size_bytes=1,
            section_count=0,
            loaded_at="2026-11-07 10:00:00",
            last_accessed="2026-11-07 10:00:00",
        )

        assert info.document_id is None
        assert info.document_kind is None

    def test_the_new_fields_accept_a_judgment(self) -> None:
        output = ContentOutput(
            eli="SAOS/554697",
            document_id="SAOS/554697",
            document_kind=DocumentKind.JUDGMENT,
            section_title="Spis treści",
            content="x",
            page_info=_page_info(),
        )

        dumped = output.model_dump(mode="json")
        assert dumped["document_kind"] == "judgment"
        assert dumped["document_id"] == dumped["eli"] == "SAOS/554697"
