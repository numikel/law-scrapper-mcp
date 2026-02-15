"""Tests for ResultStore service."""

from __future__ import annotations

import time

import pytest

from law_scrapper_mcp.models.tool_outputs import ActSummaryOutput
from law_scrapper_mcp.services.result_store import ResultSetNotFoundError, ResultStore


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
        _make_act("DU/2024/2", "Rozporządzenie Ministra Zdrowia", act_type="Rozporządzenie", promulgation_date="2024-03-15"),
        _make_act("DU/2024/3", "Ustawa o ochronie danych osobowych", act_type="Ustawa", promulgation_date="2024-06-01"),
        _make_act("DU/2024/4", "Rozporządzenie w sprawie transportu", act_type="Rozporządzenie", promulgation_date="2024-07-20"),
        _make_act("DU/2024/5", "Obwieszczenie Ministra Zdrowia", act_type="Obwieszczenie", promulgation_date="2024-09-01", status="akt jednorazowy"),
    ]


class TestResultStore:
    async def test_store_returns_incremental_ids(self, store: ResultStore, sample_results: list[ActSummaryOutput]) -> None:
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

    async def test_filter_by_regex_pattern_title(self, store: ResultStore, sample_results: list[ActSummaryOutput]) -> None:
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
        assert dates == sorted(dates, reverse=True)

    async def test_filter_limit(self, store: ResultStore, sample_results: list[ActSummaryOutput]) -> None:
        rs_id = await store.store(sample_results, "test", 5)
        filtered, _ = await store.filter_results(rs_id, limit=2)
        assert len(filtered) == 2

    async def test_filter_nonexistent_set_raises(self, store: ResultStore) -> None:
        with pytest.raises(ResultSetNotFoundError, match="Zestaw wyników 'rs_999' nie istnieje lub wygasł"):
            await store.filter_results("rs_999")

    async def test_filter_invalid_regex_raises(self, store: ResultStore, sample_results: list[ActSummaryOutput]) -> None:
        rs_id = await store.store(sample_results, "test", 5)
        with pytest.raises(ValueError, match="Invalid regex"):
            await store.filter_results(rs_id, pattern="[invalid")

    async def test_filter_invalid_field_defaults_to_title(self, store: ResultStore, sample_results: list[ActSummaryOutput]) -> None:
        rs_id = await store.store(sample_results, "test", 5)
        # Invalid field should default to title
        filtered, _ = await store.filter_results(rs_id, pattern="podatk", field="nonexistent")
        assert len(filtered) == 1

    async def test_filter_empty_results(self, store: ResultStore, sample_results: list[ActSummaryOutput]) -> None:
        rs_id = await store.store(sample_results, "test", 5)
        filtered, _ = await store.filter_results(rs_id, pattern="xyznonexistent")
        assert len(filtered) == 0

    async def test_filter_no_filters_returns_all(self, store: ResultStore, sample_results: list[ActSummaryOutput]) -> None:
        rs_id = await store.store(sample_results, "test", 5)
        filtered, original = await store.filter_results(rs_id)
        assert len(filtered) == 5
        assert original == 5
