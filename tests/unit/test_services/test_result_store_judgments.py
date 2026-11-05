"""ResultStore with sets of court judgments: kind validation, filtering, reach, listing."""

from __future__ import annotations

import pytest

from judgment_samples import make_judgment, sample_judgments
from law_scrapper_mcp.models.tool_outputs import ActSummaryOutput, DocumentKind, SetScope
from law_scrapper_mcp.services.pattern_matching import PatternValidationError
from law_scrapper_mcp.services.result_store import ResultSetTooLargeError, ResultStore

pytestmark = pytest.mark.asyncio


def _act(eli: str = "DU/2024/1") -> ActSummaryOutput:
    return ActSummaryOutput(
        eli=eli, publisher="DU", year=2024, pos=1, title="Ustawa testowa", status="akt obowiązujący"
    )


@pytest.fixture
def store() -> ResultStore:
    return ResultStore(max_sets=5, ttl=60)


async def _store_judgments(store: ResultStore, **kwargs: object) -> str:
    records = sample_judgments()
    result_set_id, _ = await store.store(
        records, "judgment search", len(records), document_kind=DocumentKind.JUDGMENT, **kwargs
    )
    return result_set_id


class TestStoreKind:
    async def test_default_kind_is_act(self, store: ResultStore) -> None:
        result_set_id, _ = await store.store([_act()], "acts", 1)
        stored = await store.get(result_set_id)
        assert stored is not None
        assert stored.document_kind is DocumentKind.ACT

    async def test_judgment_kind_is_kept(self, store: ResultStore) -> None:
        result_set_id = await _store_judgments(store)
        stored = await store.get(result_set_id)
        assert stored is not None
        assert stored.document_kind is DocumentKind.JUDGMENT
        assert len(stored.results) == 5

    async def test_acts_declared_as_judgments_are_refused(self, store: ResultStore) -> None:
        with pytest.raises(ValueError, match="JudgmentSummaryOutput"):
            await store.store([_act()], "wrong", 1, document_kind=DocumentKind.JUDGMENT)

    async def test_judgments_declared_as_acts_are_refused(self, store: ResultStore) -> None:
        with pytest.raises(ValueError, match="ActSummaryOutput"):
            await store.store(sample_judgments()[:1], "wrong", 1)

    async def test_mixed_kinds_are_refused(self, store: ResultStore) -> None:
        mixed = [make_judgment("1"), _act()]
        with pytest.raises(ValueError):
            await store.store(mixed, "mixed", 2, document_kind=DocumentKind.JUDGMENT)  # type: ignore[arg-type]

    async def test_refused_store_leaves_no_set_behind(self, store: ResultStore) -> None:
        with pytest.raises(ValueError):
            await store.store([_act()], "wrong", 1, document_kind=DocumentKind.JUDGMENT)
        assert await store.list_sets() == []

    async def test_store_stays_call_compatible_with_the_unchanged_call_shape(self, store: ResultStore) -> None:
        """The act-source search service calls `store(results, summary, total, window_offset=...)`.

        It passes neither `document_kind` nor `scope`; the call and the reach it derives must not change.
        """
        result_set_id, scope = await store.store([_act()], "unchanged", 1, window_offset=0)
        assert result_set_id == "rs_1"
        assert scope.scope is SetScope.COMPLETE
        assert scope.corpus_count == 1

        _, window = await store.store([_act()], "window", 40, window_offset=20)
        assert window.scope is SetScope.PAGE
        assert window.window_offset == 20

    async def test_judgment_reach_is_derived_like_for_acts(self, store: ResultStore) -> None:
        records = sample_judgments()
        _, complete = await store.store(records, "all", 5, document_kind=DocumentKind.JUDGMENT)
        _, window = await store.store(records, "window", 500, window_offset=40, document_kind=DocumentKind.JUDGMENT)
        assert complete.scope is SetScope.COMPLETE
        assert window.scope is SetScope.PAGE
        assert window.corpus_count == 500
        assert window.window_offset == 40


class TestListing:
    async def test_list_sets_reports_the_kind(self, store: ResultStore) -> None:
        await store.store([_act()], "acts", 1)
        await _store_judgments(store)

        page = await store.list_sets_page()

        assert [s.document_kind for s in page.sets] == [DocumentKind.ACT, DocumentKind.JUDGMENT]


class TestFilterJudgments:
    async def test_pattern_on_division_code_returns_judgments_not_results(self, store: ResultStore) -> None:
        result_set_id = await _store_judgments(store)

        output = await store.filter_and_store(result_set_id, pattern="(15|18|21)$", field="division_code")

        assert output.document_kind is DocumentKind.JUDGMENT
        assert output.results == []
        assert [j.source_id for j in output.judgments] == ["1", "2", "4"]
        assert output.original_count == 5
        assert output.filtered_count == 3
        assert output.filters_applied == {"pattern": "(15|18|21)$", "field": "division_code"}
        assert output.page_info.total_count == 3

    async def test_filtered_subset_is_stored_as_a_judgment_set_and_can_be_filtered_again(
        self, store: ResultStore
    ) -> None:
        result_set_id = await _store_judgments(store)
        first = await store.filter_and_store(result_set_id, pattern="Pracy", field="division")
        assert first.result_set_id is not None

        second = await store.filter_and_store(first.result_set_id, type_equals="Wyrok")

        assert second.source_result_set_id == first.result_set_id
        assert [j.source_id for j in second.judgments] == ["1"]
        stored = await store.get(first.result_set_id)
        assert stored is not None
        assert stored.document_kind is DocumentKind.JUDGMENT

    async def test_date_range_sort_and_year(self, store: ResultStore) -> None:
        result_set_id = await _store_judgments(store)

        ranged = await store.filter_and_store(
            result_set_id,
            date_field="judgment_date",
            date_from="2020-01-01",
            date_to="2022-12-31",
            sort_by="judgment_date",
            sort_desc=True,
        )
        by_year = await store.filter_and_store(result_set_id, year_equals=2021)

        assert [j.source_id for j in ranged.judgments] == ["4", "3", "2"]
        assert ranged.filters_applied["date_field"] == "judgment_date"
        assert ranged.filters_applied["sort_by"] == "judgment_date"
        assert [j.source_id for j in by_year.judgments] == ["3"]

    async def test_page_of_the_filtered_set(self, store: ResultStore) -> None:
        result_set_id = await _store_judgments(store)

        output = await store.filter_and_store(result_set_id, sort_by="judgment_date", limit=2, offset=1)

        assert [j.source_id for j in output.judgments] == ["1", "2"]
        assert output.page_info.total_count == 5
        assert output.page_info.next_offset == 3
        assert output.filtered_count == 5

    async def test_window_reach_is_inherited_and_empty_match_is_inconclusive(self, store: ResultStore) -> None:
        result_set_id = await _store_judgments(store, window_offset=20, scope=SetScope.PAGE)

        output = await store.filter_and_store(result_set_id, pattern="no-such-division", field="division")

        assert output.judgments == []
        assert output.result_set_id is None
        assert output.source_scope.scope is SetScope.PAGE
        assert output.no_match_is_inconclusive is True

    async def test_subset_of_a_window_stays_a_window(self, store: ResultStore) -> None:
        result_set_id = await _store_judgments(store, window_offset=20, scope=SetScope.PAGE)

        output = await store.filter_and_store(result_set_id, type_equals="Wyrok")

        assert output.result_set_scope is not None
        assert output.result_set_scope.scope is SetScope.PAGE
        assert output.result_set_scope.corpus_count is None
        assert output.no_match_is_inconclusive is False

    async def test_complete_set_gives_a_conclusive_empty_result(self, store: ResultStore) -> None:
        result_set_id = await _store_judgments(store)

        output = await store.filter_and_store(result_set_id, type_equals="Zarządzenie")

        assert output.judgments == []
        assert output.no_match_is_inconclusive is False

    async def test_empty_match_stores_nothing(self, store: ResultStore) -> None:
        result_set_id = await _store_judgments(store)
        before = len(await store.list_sets())

        await store.filter_and_store(result_set_id, type_equals="Zarządzenie")

        assert len(await store.list_sets()) == before


class TestRefusals:
    async def test_status_equals_is_refused_with_a_polish_error(self, store: ResultStore) -> None:
        result_set_id = await _store_judgments(store)
        with pytest.raises(ValueError, match="status_equals"):
            await store.filter_and_store(result_set_id, status_equals="akt obowiązujący")

    async def test_act_field_and_act_date_field_are_refused(self, store: ResultStore) -> None:
        result_set_id = await _store_judgments(store)
        with pytest.raises(ValueError, match="'eli'"):
            await store.filter_and_store(result_set_id, pattern="x", field="eli")
        with pytest.raises(ValueError, match="'effective_date'"):
            await store.filter_and_store(result_set_id, date_field="effective_date", date_from="2020-01-01")
        with pytest.raises(ValueError, match="'pos'"):
            await store.filter_and_store(result_set_id, sort_by="pos")

    async def test_pattern_limits_of_the_store_apply(self) -> None:
        store = ResultStore(max_sets=5, ttl=60, max_pattern_length=10)
        result_set_id = await _store_judgments(store)
        with pytest.raises(PatternValidationError):
            await store.filter_and_store(result_set_id, pattern="a" * 11)

    async def test_oversized_judgment_set_is_refused_before_filtering(self) -> None:
        store = ResultStore(max_sets=5, ttl=60, max_records=3)
        result_set_id = await _store_judgments(store)
        with pytest.raises(ResultSetTooLargeError):
            await store.filter_and_store(result_set_id, pattern="x")

    async def test_the_act_only_tuple_method_refuses_a_judgment_set(self, store: ResultStore) -> None:
        result_set_id = await _store_judgments(store)
        with pytest.raises(ValueError, match="orzeczenia"):
            await store.filter_results(result_set_id, pattern="x")


class TestActPathUnchanged:
    async def test_act_set_still_returns_results_and_kind_act(self, store: ResultStore) -> None:
        result_set_id, _ = await store.store([_act("DU/2024/1"), _act("DU/2024/2")], "acts", 2)

        output = await store.filter_and_store(result_set_id, pattern="Ustawa")

        assert output.document_kind is DocumentKind.ACT
        assert [a.eli for a in output.results] == ["DU/2024/1", "DU/2024/2"]
        assert output.judgments == []

    async def test_act_path_still_ignores_an_unknown_date_field_and_field(self, store: ResultStore) -> None:
        result_set_id, _ = await store.store([_act()], "acts", 1)

        output = await store.filter_and_store(
            result_set_id, pattern="Ustawa", field="signature", date_field="judgment_date", date_from="2020-01-01"
        )

        assert len(output.results) == 1
