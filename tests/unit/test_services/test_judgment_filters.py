"""Tests for the shared `filter_results` filters on judgment sets."""

from __future__ import annotations

import pytest

from judgment_samples import make_judgment, sample_judgments
from law_scrapper_mcp.models.pagination import PageInfo, PageUnit
from law_scrapper_mcp.models.tool_outputs import (
    DocumentKind,
    FilterOutput,
    ResultSetScope,
    SetScope,
)
from law_scrapper_mcp.services.judgment_filters import (
    JUDGMENT_DATE_FIELDS,
    JUDGMENT_SEARCHABLE_FIELDS,
    JUDGMENT_SORT_FIELDS,
    apply_judgment_filters,
    judgment_filter_hints,
    reject_act_only_filters,
)
from law_scrapper_mcp.services.pattern_matching import compile_pattern


def _compile(pattern: str):  # noqa: ANN202 - CompiledPattern is Any
    return compile_pattern(pattern, max_length=512)


def _ids(records: list) -> list[str]:
    return [r.source_id for r in records]


class TestPatternFilter:
    def test_default_field_is_the_citation_title(self) -> None:
        result = apply_judgment_filters(sample_judgments(), compiled_pattern=_compile("Alfie"))
        assert _ids(result) == ["1"]

    def test_pattern_is_case_insensitive(self) -> None:
        result = apply_judgment_filters(sample_judgments(), compiled_pattern=_compile("karny"), field="division")
        assert _ids(result) == ["3"]

    @pytest.mark.parametrize(
        ("field", "pattern", "expected"),
        [
            ("signature", r"^IV P", ["2"]),
            ("court", "Rejonowy", ["2", "3"]),
            ("type", "Uzasadnienie", ["4"]),
            ("division", "Pracy", ["1", "2"]),
            ("division_code", "(15|18|21)$", ["1", "2", "4"]),
            ("division_code", "(15|21)$", ["1", "2"]),
            ("excerpt", "testowego", ["1", "2", "3", "5"]),
        ],
    )
    def test_every_searchable_field(self, field: str, pattern: str, expected: list[str]) -> None:
        result = apply_judgment_filters(sample_judgments(), compiled_pattern=_compile(pattern), field=field)
        assert _ids(result) == expected

    def test_record_without_the_field_never_matches(self) -> None:
        # Record 5 has no division and no division code; ".*" matches any present value, even an empty one.
        result = apply_judgment_filters(sample_judgments(), compiled_pattern=_compile(".*"), field="division")
        assert "5" not in _ids(result)

    def test_every_searchable_field_is_declared(self) -> None:
        assert set(JUDGMENT_SEARCHABLE_FIELDS) == {
            "title",
            "signature",
            "court",
            "type",
            "division",
            "division_code",
            "excerpt",
        }

    def test_unknown_field_lists_valid_values(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            apply_judgment_filters(sample_judgments(), compiled_pattern=_compile("x"), field="status")
        message = str(excinfo.value)
        assert "'status'" in message
        for valid in JUDGMENT_SEARCHABLE_FIELDS:
            assert f"'{valid}'" in message

    def test_field_is_not_validated_without_a_pattern(self) -> None:
        # `field` is meaningless without a pattern; the act path treats it the same way.
        assert len(apply_judgment_filters(sample_judgments(), field="status")) == 5


class TestExactFilters:
    def test_type_equals_is_exact(self) -> None:
        result = apply_judgment_filters(sample_judgments(), type_equals="Wyrok")
        assert _ids(result) == ["1", "3", "5"]
        assert apply_judgment_filters(sample_judgments(), type_equals="wyrok") == []

    def test_year_equals_uses_the_judgment_date(self) -> None:
        assert _ids(apply_judgment_filters(sample_judgments(), year_equals=2020)) == ["2"]

    def test_year_equals_skips_a_missing_date(self) -> None:
        records = [make_judgment("1", judgment_date=None), make_judgment("2", judgment_date="2020-01-01")]
        assert _ids(apply_judgment_filters(records, year_equals=2020)) == ["2"]

    def test_year_equals_ignores_a_malformed_date(self) -> None:
        records = [make_judgment("1", judgment_date="n/a"), make_judgment("2", judgment_date="20")]
        assert apply_judgment_filters(records, year_equals=2020) == []


class TestDateRange:
    def test_range_is_inclusive_and_skips_missing_dates(self) -> None:
        result = apply_judgment_filters(
            sample_judgments(), date_field="judgment_date", date_from="2020-06-10", date_to="2021-09-30"
        )
        assert _ids(result) == ["2", "3"]

    def test_open_ended_bounds(self) -> None:
        after = apply_judgment_filters(sample_judgments(), date_field="judgment_date", date_from="2021-01-01")
        before = apply_judgment_filters(sample_judgments(), date_field="judgment_date", date_to="2019-12-31")
        assert _ids(after) == ["3", "4"]
        assert _ids(before) == ["1"]

    def test_date_field_without_bounds_keeps_everything(self) -> None:
        assert len(apply_judgment_filters(sample_judgments(), date_field="judgment_date")) == 5

    def test_act_date_field_is_refused_and_valid_ones_listed(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            apply_judgment_filters(sample_judgments(), date_field="promulgation_date", date_from="2020-01-01")
        message = str(excinfo.value)
        assert "'promulgation_date'" in message
        for valid in JUDGMENT_DATE_FIELDS:
            assert f"'{valid}'" in message


class TestSorting:
    def test_sort_by_date_ascending_puts_missing_dates_first(self) -> None:
        result = apply_judgment_filters(sample_judgments(), sort_by="judgment_date")
        assert _ids(result) == ["5", "1", "2", "3", "4"]

    def test_sort_descending(self) -> None:
        result = apply_judgment_filters(sample_judgments(), sort_by="judgment_date", sort_desc=True)
        assert _ids(result) == ["4", "3", "2", "1", "5"]

    def test_sort_by_type_uses_the_polish_label(self) -> None:
        result = apply_judgment_filters(sample_judgments(), sort_by="type")
        assert [r.judgment_type for r in result] == ["Postanowienie", "Uzasadnienie", "Wyrok", "Wyrok", "Wyrok"]

    def test_unknown_sort_field_lists_valid_values(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            apply_judgment_filters(sample_judgments(), sort_by="year")
        message = str(excinfo.value)
        assert "'year'" in message
        for valid in JUDGMENT_SORT_FIELDS:
            assert f"'{valid}'" in message

    def test_input_is_not_mutated(self) -> None:
        records = sample_judgments()
        before = list(records)
        apply_judgment_filters(records, sort_by="judgment_date", type_equals="Wyrok")
        assert records == before


class TestCombination:
    def test_filters_combine_with_and_semantics(self) -> None:
        result = apply_judgment_filters(
            sample_judgments(),
            compiled_pattern=_compile("(15|18|21)$"),
            field="division_code",
            type_equals="Wyrok",
        )
        assert _ids(result) == ["1"]


class TestActOnlyFilters:
    def test_status_equals_is_refused(self) -> None:
        with pytest.raises(ValueError, match="status_equals"):
            reject_act_only_filters("akt obowiązujący")

    @pytest.mark.parametrize("value", [None, ""])
    def test_absent_status_is_accepted(self, value: str | None) -> None:
        reject_act_only_filters(value)


def _output(
    *,
    judgments: list | None = None,
    result_set_id: str | None = "rs_2",
    inconclusive: bool = False,
    scope: SetScope = SetScope.PAGE,
    corpus_count: int | None = 500,
) -> FilterOutput:
    return FilterOutput(
        source_result_set_id="rs_1",
        result_set_id=result_set_id,
        results=[],
        document_kind=DocumentKind.JUDGMENT,
        judgments=judgments or [],
        original_count=20,
        filtered_count=len(judgments or []),
        source_scope=ResultSetScope(scope=scope, stored_count=20, window_offset=0, corpus_count=corpus_count),
        no_match_is_inconclusive=inconclusive,
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


class TestHints:
    def test_hit_hints_point_at_further_filtering_and_the_source_details(self) -> None:
        hints = judgment_filter_hints(_output(judgments=sample_judgments()[:2]), filter_max_records=100)

        assert any(h.tool == "filter_results" and h.parameters == {"result_set_id": "rs_2"} for h in hints)
        details = hints[0]
        assert "testsource" in details.message
        assert "source_id" in details.message
        assert details.tool is None, "the core must not name a module tool"

    def test_no_further_filtering_hint_without_a_stored_set(self) -> None:
        hints = judgment_filter_hints(
            _output(judgments=sample_judgments()[:1], result_set_id=None), filter_max_records=100
        )
        assert all(h.tool != "filter_results" for h in hints)

    def test_inconclusive_window_sentence(self) -> None:
        hints = judgment_filter_hints(_output(inconclusive=True), filter_max_records=100)

        assert len(hints) == 1
        message = hints[0].message
        assert "OKNO" in message
        assert "20 rekordów z 500 dopasowań" in message
        assert "NIE dowodzi" in message
        assert "100" in message
        assert hints[0].tool is None

    def test_inconclusive_window_with_unknown_corpus(self) -> None:
        hints = judgment_filter_hints(_output(inconclusive=True, corpus_count=None), filter_max_records=100)
        assert "20 rekordów." in hints[0].message

    def test_no_hints_for_an_empty_conclusive_result(self) -> None:
        assert judgment_filter_hints(_output(scope=SetScope.COMPLETE), filter_max_records=100) == []
