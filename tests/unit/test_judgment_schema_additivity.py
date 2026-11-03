"""The judgment support is additive: act-shaped consumers of the outputs see nothing new that is required."""

from __future__ import annotations

from law_scrapper_mcp.models.pagination import PageInfo, PageUnit
from law_scrapper_mcp.models.tool_outputs import (
    ActSummaryOutput,
    DocumentKind,
    FilterOutput,
    JudgmentSummaryOutput,
    ResultSetInfo,
    ResultSetScope,
    SetScope,
)

# Written out by hand, as they were before judgments existed. A new required field on either output
# would break every client that builds or validates the old shape, so this list may only grow by
# optional fields.
FILTER_OUTPUT_REQUIRED = {
    "source_result_set_id",
    "results",
    "original_count",
    "filtered_count",
    "source_scope",
    "page_info",
}
FILTER_OUTPUT_PREVIOUS_PROPERTIES = FILTER_OUTPUT_REQUIRED | {
    "result_set_id",
    "filters_applied",
    "result_set_scope",
    "no_match_is_inconclusive",
}
RESULT_SET_INFO_REQUIRED = {"result_set_id", "query_summary", "result_count", "total_count", "scope", "created_at"}
ACT_SUMMARY_PROPERTIES = {
    "eli",
    "publisher",
    "year",
    "pos",
    "title",
    "status",
    "type",
    "promulgation_date",
    "effective_date",
    "in_force",
}


def _schema(model: type) -> dict:  # type: ignore[type-arg]
    return model.model_json_schema()  # type: ignore[attr-defined, no-any-return]


class TestFilterOutputSchema:
    def test_required_list_is_unchanged(self) -> None:
        assert set(_schema(FilterOutput)["required"]) == FILTER_OUTPUT_REQUIRED

    def test_previous_properties_are_all_still_there(self) -> None:
        assert set(_schema(FilterOutput)["properties"]) >= FILTER_OUTPUT_PREVIOUS_PROPERTIES

    def test_only_the_two_judgment_properties_were_added(self) -> None:
        added = set(_schema(FilterOutput)["properties"]) - FILTER_OUTPUT_PREVIOUS_PROPERTIES
        assert added == {"document_kind", "judgments"}

    def test_results_stays_act_typed(self) -> None:
        items = _schema(FilterOutput)["properties"]["results"]["items"]
        assert items == {"$ref": "#/$defs/ActSummaryOutput"}

    def test_envelope_of_the_tool_output_is_untouched(self) -> None:
        from law_scrapper_mcp.models.tool_outputs import EnrichedResponse

        schema = _schema(EnrichedResponse[FilterOutput])
        assert set(schema["properties"]) == {"data", "hints"}
        assert schema["required"] == ["data"]


class TestResultSetInfoSchema:
    def test_required_list_is_unchanged(self) -> None:
        assert set(_schema(ResultSetInfo)["required"]) == RESULT_SET_INFO_REQUIRED

    def test_kind_is_an_optional_addition(self) -> None:
        properties = _schema(ResultSetInfo)["properties"]
        assert "document_kind" in properties
        assert "document_kind" not in _schema(ResultSetInfo)["required"]


class TestActRecordUntouched:
    def test_act_summary_has_exactly_its_old_properties(self) -> None:
        assert set(_schema(ActSummaryOutput)["properties"]) == ACT_SUMMARY_PROPERTIES


class TestOldShapeStillBuilds:
    def test_filter_output_built_the_old_way(self) -> None:
        output = FilterOutput(
            source_result_set_id="rs_1",
            results=[],
            original_count=0,
            filtered_count=0,
            source_scope=ResultSetScope(scope=SetScope.COMPLETE, stored_count=0, window_offset=0, corpus_count=0),
            page_info=PageInfo(
                limit=20,
                offset=0,
                returned_count=0,
                total_count=0,
                was_truncated=False,
                next_offset=None,
                unit=PageUnit.ITEMS,
            ),
        )

        dumped = output.model_dump(mode="json")
        assert dumped["document_kind"] == "act"
        assert dumped["judgments"] == []

    def test_result_set_info_built_the_old_way(self) -> None:
        info = ResultSetInfo(
            result_set_id="rs_1",
            query_summary="q",
            result_count=1,
            total_count=1,
            scope=SetScope.COMPLETE,
            created_at="2026-11-03 12:00:00",
        )
        assert info.document_kind is DocumentKind.ACT


class TestJudgmentRecord:
    def test_required_fields(self) -> None:
        required = set(_schema(JudgmentSummaryOutput)["required"])
        assert required == {"source", "source_id", "signature", "title", "court", "judgment_type", "source_url"}

    def test_optional_fields_default_to_none(self) -> None:
        record = JudgmentSummaryOutput(
            source="s",
            source_id="1",
            signature="I A 1/20",
            title="t",
            court="c",
            judgment_type="Wyrok",
            source_url="https://example.test/1",
        )
        assert record.portal_url is None
        assert record.ecli is None
        assert record.excerpt is None
        assert record.division_code is None

    def test_no_judge_or_text_fields_exist(self) -> None:
        """The record carries an excerpt only: no judges, no full text."""
        properties = set(_schema(JudgmentSummaryOutput)["properties"])
        assert not {"judges", "text", "content", "reasoning"} & properties
