"""Tests for ResultStore service."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from law_scrapper_mcp.models.pagination import PageUnit
from law_scrapper_mcp.models.tool_outputs import ActSummaryOutput
from law_scrapper_mcp.services.pattern_matching import (
    CompiledPattern,
    PatternValidationError,
    compile_pattern,
)
from law_scrapper_mcp.services.result_store import ResultSetNotFoundError, ResultSetTooLargeError, ResultStore


def _make_act(
    eli: str = "DU/2024/1",
    title: str = "Test Act",
    status: str = "akt obowiązujący",
    act_type: str | None = "Ustawa",
    year: int = 2024,
    pos: int = 1,
    promulgation_date: str | None = "2024-01-15",
    effective_date: str | None = "2024-02-01",
) -> ActSummaryOutput:
    return ActSummaryOutput(
        eli=eli,
        publisher="DU",
        year=year,
        pos=pos,
        title=title,
        status=status,
        type=act_type,
        promulgation_date=promulgation_date,
        effective_date=effective_date,
    )


@pytest.fixture
def store() -> ResultStore:
    return ResultStore(max_sets=5, ttl=60)


@pytest.fixture
def sample_results() -> list[ActSummaryOutput]:
    return [
        _make_act("DU/2024/1", "Ustawa o podatku dochodowym", act_type="Ustawa", promulgation_date="2024-01-10"),
        _make_act(
            "DU/2024/2", "Rozporządzenie Ministra Zdrowia", act_type="Rozporządzenie", promulgation_date="2024-03-15"
        ),
        _make_act("DU/2024/3", "Ustawa o ochronie danych osobowych", act_type="Ustawa", promulgation_date="2024-06-01"),
        _make_act(
            "DU/2024/4",
            "Rozporządzenie w sprawie transportu",
            act_type="Rozporządzenie",
            promulgation_date="2024-07-20",
        ),
        _make_act(
            "DU/2024/5",
            "Obwieszczenie Ministra Zdrowia",
            act_type="Obwieszczenie",
            promulgation_date="2024-09-01",
            status="akt jednorazowy",
        ),
    ]


class TestResultStore:
    async def test_store_returns_incremental_ids(
        self, store: ResultStore, sample_results: list[ActSummaryOutput]
    ) -> None:
        id1 = await store.store(sample_results[:2], "query1", 2)
        id2 = await store.store(sample_results[2:], "query2", 3)
        assert id1 == "rs_1"
        assert id2 == "rs_2"

    async def test_get_returns_stored_results(self, store: ResultStore, sample_results: list[ActSummaryOutput]) -> None:
        rs_id = await store.store(sample_results, "test query", 5)
        rs = await store.get(rs_id)
        assert rs is not None
        assert len(rs.results) == 5
        assert rs.query_summary == "test query"
        assert rs.total_count == 5

    async def test_get_nonexistent_returns_none(self, store: ResultStore) -> None:
        assert await store.get("rs_999") is None

    async def test_list_sets(self, store: ResultStore, sample_results: list[ActSummaryOutput]) -> None:
        await store.store(sample_results[:2], "query1", 2)
        await store.store(sample_results[2:], "query2", 3)
        sets = await store.list_sets()
        assert len(sets) == 2
        assert sets[0]["result_set_id"] == "rs_1"
        assert sets[1]["result_set_id"] == "rs_2"

    async def test_evicts_lru_when_full(self, store: ResultStore, sample_results: list[ActSummaryOutput]) -> None:
        # Fill store to max (5)
        for i in range(5):
            await store.store([sample_results[0]], f"query{i}", 1)
        # Adding 6th should evict the oldest
        await store.store([sample_results[1]], "query5", 1)
        assert await store.get("rs_1") is None  # evicted
        assert await store.get("rs_6") is not None

    async def test_evicts_expired(self) -> None:
        store = ResultStore(max_sets=5, ttl=0)  # TTL=0 → immediate expiry
        act = _make_act()
        rs_id = await store.store([act], "query", 1)
        time.sleep(0.01)
        assert await store.get(rs_id) is None


class TestResultStoreFiltering:
    async def test_filter_by_type_equals(self, store: ResultStore, sample_results: list[ActSummaryOutput]) -> None:
        rs_id = await store.store(sample_results, "test", 5)
        filtered, original = await store.filter_results(rs_id, type_equals="Ustawa")
        assert original == 5
        assert len(filtered) == 2
        assert all(r.type == "Ustawa" for r in filtered)

    async def test_filter_by_status_equals(self, store: ResultStore, sample_results: list[ActSummaryOutput]) -> None:
        rs_id = await store.store(sample_results, "test", 5)
        filtered, _ = await store.filter_results(rs_id, status_equals="akt jednorazowy")
        assert len(filtered) == 1
        assert filtered[0].eli == "DU/2024/5"

    async def test_filter_by_year_equals(self, store: ResultStore) -> None:
        results = [_make_act(year=2023), _make_act(year=2024), _make_act(year=2024)]
        rs_id = await store.store(results, "test", 3)
        filtered, _ = await store.filter_results(rs_id, year_equals=2024)
        assert len(filtered) == 2

    async def test_filter_by_regex_pattern_title(
        self, store: ResultStore, sample_results: list[ActSummaryOutput]
    ) -> None:
        rs_id = await store.store(sample_results, "test", 5)
        filtered, _ = await store.filter_results(rs_id, pattern="zdrow|Zdrowia")
        assert len(filtered) == 2  # Minister Zdrowia appears in 2 titles

    async def test_filter_by_regex_pattern_or(self, store: ResultStore, sample_results: list[ActSummaryOutput]) -> None:
        rs_id = await store.store(sample_results, "test", 5)
        filtered, _ = await store.filter_results(rs_id, pattern="podatk|transport")
        assert len(filtered) == 2

    async def test_filter_by_regex_field_eli(self, store: ResultStore, sample_results: list[ActSummaryOutput]) -> None:
        rs_id = await store.store(sample_results, "test", 5)
        filtered, _ = await store.filter_results(rs_id, pattern="DU/2024/[12]$", field="eli")
        assert len(filtered) == 2

    async def test_filter_by_date_range(self, store: ResultStore, sample_results: list[ActSummaryOutput]) -> None:
        rs_id = await store.store(sample_results, "test", 5)
        filtered, _ = await store.filter_results(
            rs_id,
            date_field="promulgation_date",
            date_from="2024-01-01",
            date_to="2024-06-01",
        )
        assert len(filtered) == 3

    async def test_filter_combined(self, store: ResultStore, sample_results: list[ActSummaryOutput]) -> None:
        rs_id = await store.store(sample_results, "test", 5)
        filtered, _ = await store.filter_results(
            rs_id,
            type_equals="Rozporządzenie",
            pattern="zdrow|Zdrowia",
        )
        assert len(filtered) == 1
        assert "Zdrowia" in filtered[0].title

    async def test_filter_sort_by(self, store: ResultStore, sample_results: list[ActSummaryOutput]) -> None:
        rs_id = await store.store(sample_results, "test", 5)
        filtered, _ = await store.filter_results(rs_id, sort_by="promulgation_date", sort_desc=True)
        dates = [r.promulgation_date for r in filtered]
        assert dates == sorted(dates, key=lambda d: d or "", reverse=True)

    async def test_filter_limit(self, store: ResultStore, sample_results: list[ActSummaryOutput]) -> None:
        rs_id = await store.store(sample_results, "test", 5)
        filtered, _ = await store.filter_results(rs_id, limit=2)
        assert len(filtered) == 2

    async def test_filter_nonexistent_set_raises(self, store: ResultStore) -> None:
        with pytest.raises(ResultSetNotFoundError, match="Zestaw wyników 'rs_999' nie istnieje lub wygasł"):
            await store.filter_results("rs_999")

    async def test_filter_invalid_regex_raises(
        self, store: ResultStore, sample_results: list[ActSummaryOutput]
    ) -> None:
        rs_id = await store.store(sample_results, "test", 5)
        with pytest.raises(PatternValidationError, match="nie jest obsługiwany"):
            await store.filter_results(rs_id, pattern="[invalid")

    async def test_filter_invalid_field_defaults_to_title(
        self, store: ResultStore, sample_results: list[ActSummaryOutput]
    ) -> None:
        rs_id = await store.store(sample_results, "test", 5)
        # Invalid field should default to title
        filtered, _ = await store.filter_results(rs_id, pattern="podatk", field="nonexistent")
        assert len(filtered) == 1

    async def test_filter_empty_results(self, store: ResultStore, sample_results: list[ActSummaryOutput]) -> None:
        rs_id = await store.store(sample_results, "test", 5)
        filtered, _ = await store.filter_results(rs_id, pattern="xyznonexistent")
        assert len(filtered) == 0

    async def test_filter_no_filters_returns_all(
        self, store: ResultStore, sample_results: list[ActSummaryOutput]
    ) -> None:
        rs_id = await store.store(sample_results, "test", 5)
        filtered, original = await store.filter_results(rs_id)
        assert len(filtered) == 5
        assert original == 5


# Title of realistic length — audit measured max 495 characters on 2024 acts.
_REALISTIC_LONG_TITLE = (
    "Rozporządzenie Ministra Rozwoju i Technologii z dnia 12 kwietnia 2024 r. "
    "zmieniające rozporządzenie w sprawie szczegółowego zakresu i formy projektu "
    "budowlanego oraz warunków technicznych, jakim powinny odpowiadać budynki "
    "i ich usytuowanie, w zakresie wymagań ochrony przeciwpożarowej"
)


class TestResultStoreReDoSRegression:
    """Catastrophic pattern must not freeze the process"""

    async def test_filter_engine_is_re2_not_re(
        self, store: ResultStore, sample_results: list[ActSummaryOutput]
    ) -> None:
        """Engine assertion on a SAFE pattern, run BEFORE the catastrophic test.

        Intentionally a separate test and a separate harmless pattern ("Ustawa"):
        if `compile_pattern` became unimportable or fell back to `re` internally,
        this test would either raise `AttributeError` immediately on entering
        `patch(...)`, or finish at once — it would not hang the CI job, unlike
        placing the same assertion inside the `with` block around the
        catastrophic pattern (where code after `filter_results` would never be
        reached on a regression to `re`).
        """
        rs_id = await store.store(sample_results, "test", 5)

        captured: list[CompiledPattern] = []

        def _spy_compile_pattern(
            pattern: str,
            *,
            max_length: int,
            limit_was_clamped: bool = False,
        ) -> CompiledPattern:
            compiled = compile_pattern(
                pattern,
                max_length=max_length,
                limit_was_clamped=limit_was_clamped,
            )
            captured.append(compiled)
            return compiled

        with patch(
            "law_scrapper_mcp.services.result_store.compile_pattern",
            side_effect=_spy_compile_pattern,
        ):
            await store.filter_results(rs_id, pattern="Ustawa")

        assert captured, "compile_pattern was not called"
        assert type(captured[0]).__module__.startswith("re2")

    @pytest.mark.timeout(5)
    async def test_catastrophic_pattern_returns_promptly(self, store: ResultStore) -> None:
        results = [_make_act(f"DU/2024/{i}", _REALISTIC_LONG_TITLE) for i in range(1, 11)]
        rs_id = await store.store(results, "test", len(results))

        filtered, original = await store.filter_results(rs_id, pattern="(.+)+!", field="title")

        assert filtered == []
        assert original == 10

    @pytest.mark.timeout(5)
    async def test_documented_patterns_still_work(
        self, store: ResultStore, sample_results: list[ActSummaryOutput]
    ) -> None:
        rs_id = await store.store(sample_results, "test", 5)

        health, _ = await store.filter_results(rs_id, pattern="zdrow|Minister Zdrowia|apteka|lekar")
        wildcard, _ = await store.filter_results(rs_id, pattern="Ustawa.*danych")
        taxes, _ = await store.filter_results(rs_id, pattern="podatek|VAT|akcyza")

        assert len(health) == 2  # two titles with "Zdrowia" — case-insensitive match
        assert len(wildcard) == 1
        # Fixture has "Ustawa o podatku dochodowym"; the pattern looks for "podatek".
        # Zero hits matches pre-engine-change behaviour.
        assert len(taxes) == 0

    @pytest.mark.timeout(5)
    async def test_alternation_matches_when_form_agrees(self, store: ResultStore) -> None:
        """Positive control for an alternation pattern — without it the test above is blind."""
        results = [_make_act("DU/2024/9", "Ustawa o podatek akcyza VAT")]
        rs_id = await store.store(results, "test", 1)

        filtered, _ = await store.filter_results(rs_id, pattern="podatek|VAT|akcyza")

        assert len(filtered) == 1

    async def test_lookaround_is_rejected_with_polish_message(
        self, store: ResultStore, sample_results: list[ActSummaryOutput]
    ) -> None:
        rs_id = await store.store(sample_results, "test", 5)

        with pytest.raises(PatternValidationError) as exc_info:
            await store.filter_results(rs_id, pattern="(?<=Ustawa)o")

        assert "nie jest obsługiwany" in str(exc_info.value)

    async def test_pattern_over_limit_is_rejected(self, sample_results: list[ActSummaryOutput]) -> None:
        store = ResultStore(max_sets=5, ttl=60, max_pattern_length=64)
        rs_id = await store.store(sample_results, "test", 5)

        with pytest.raises(PatternValidationError, match="za długi"):
            await store.filter_results(rs_id, pattern="a" * 65)

    async def test_pattern_at_limit_is_accepted(self, sample_results: list[ActSummaryOutput]) -> None:
        """Boundary: a pattern whose length equals the limit exactly is not rejected."""
        store = ResultStore(max_sets=5, ttl=60, max_pattern_length=64)
        rs_id = await store.store(sample_results, "test", 5)

        filtered, _ = await store.filter_results(rs_id, pattern="a" * 64)

        assert filtered == []


class TestResultStoreRecordCap:
    """Refuse the call instead of returning a partial result"""

    async def test_oversized_set_is_refused(self, sample_results: list[ActSummaryOutput]) -> None:
        store = ResultStore(max_sets=5, ttl=60, max_records=3)
        rs_id = await store.store(sample_results, "test", 5)

        with pytest.raises(ResultSetTooLargeError) as exc_info:
            await store.filter_results(rs_id, pattern="Ustawa")

        assert exc_info.value.size == 5
        assert exc_info.value.limit == 3
        assert "Zawęź" in str(exc_info.value)

    async def test_refusal_applies_without_pattern_too(self, sample_results: list[ActSummaryOutput]) -> None:
        """Refusal applies to the call, not only to the regex path."""
        store = ResultStore(max_sets=5, ttl=60, max_records=3)
        rs_id = await store.store(sample_results, "test", 5)

        with pytest.raises(ResultSetTooLargeError):
            await store.filter_results(rs_id, type_equals="Ustawa")

    async def test_set_at_limit_is_processed(self, sample_results: list[ActSummaryOutput]) -> None:
        store = ResultStore(max_sets=5, ttl=60, max_records=5)
        rs_id = await store.store(sample_results, "test", 5)

        filtered, original = await store.filter_results(rs_id, type_equals="Ustawa")

        assert original == 5
        assert len(filtered) == 2


class TestResultStoreFilterAndStore:
    async def test_filter_and_store_persists_chained_result_set(
        self, store: ResultStore, sample_results: list[ActSummaryOutput]
    ) -> None:
        source_id = await store.store(sample_results, "search query", len(sample_results))

        output = await store.filter_and_store(
            source_id,
            type_equals="Ustawa",
        )

        assert output.source_result_set_id == source_id
        assert output.result_set_id == "rs_2"
        assert output.filtered_count == 2
        assert output.original_count == len(sample_results)
        assert all(result.type == "Ustawa" for result in output.results)
        assert len(output.results) == output.page_info.returned_count
        assert output.filtered_count == output.page_info.total_count
        assert output.page_info.unit == PageUnit.ITEMS

        stored = await store.get(output.result_set_id)
        assert stored is not None
        assert stored.query_summary.startswith(f"filtered({source_id}):")
        assert "type_equals=Ustawa" in stored.query_summary

        chained_output = await store.filter_and_store(
            output.result_set_id,
            pattern="podatku",
            field="title",
        )

        assert chained_output.source_result_set_id == output.result_set_id
        assert chained_output.result_set_id == "rs_3"
        assert chained_output.original_count == output.filtered_count
        assert chained_output.filtered_count == 1
        assert chained_output.results[0].eli == "DU/2024/1"
        assert chained_output.filters_applied == {
            "pattern": "podatku",
            "field": "title",
        }

        chained_stored = await store.get(chained_output.result_set_id)
        assert chained_stored is not None
        assert chained_stored.query_summary.startswith(f"filtered({output.result_set_id}):")
        assert "pattern=podatku" in chained_stored.query_summary
        assert len(chained_stored.results) == 1

    async def test_filter_and_store_records_applied_filters(
        self, store: ResultStore, sample_results: list[ActSummaryOutput]
    ) -> None:
        source_id = await store.store(sample_results, "search query", len(sample_results))

        output = await store.filter_and_store(
            source_id,
            pattern="Ustawa",
            field="title",
            sort_by="title",
            sort_desc=True,
            limit=1,
        )

        assert output.filters_applied == {
            "pattern": "Ustawa",
            "field": "title",
            "sort_by": "title",
            "sort_desc": True,
            "limit": 1,
        }
        assert output.filtered_count == 1

    async def test_filter_and_store_leaves_result_set_id_none_for_empty_matches(
        self, store: ResultStore, sample_results: list[ActSummaryOutput]
    ) -> None:
        source_id = await store.store(sample_results, "search query", len(sample_results))

        output = await store.filter_and_store(
            source_id,
            pattern="nonexistent-pattern-xyz",
        )

        assert output.results == []
        assert output.filtered_count == 0
        assert output.result_set_id is None
        assert output.filters_applied["pattern"] == "nonexistent-pattern-xyz"
