"""Tests for ResultStore service."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from law_scrapper_mcp.models.tool_outputs import ActSummaryOutput
from law_scrapper_mcp.services.pattern_matching import PatternValidationError, compile_pattern
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


# Tytuł o realnej długości — audyt zmierzył maks. 495 znaków na aktach z 2024 r.
_REALISTIC_LONG_TITLE = (
    "Rozporządzenie Ministra Rozwoju i Technologii z dnia 12 kwietnia 2024 r. "
    "zmieniające rozporządzenie w sprawie szczegółowego zakresu i formy projektu "
    "budowlanego oraz warunków technicznych, jakim powinny odpowiadać budynki "
    "i ich usytuowanie, w zakresie wymagań ochrony przeciwpożarowej"
)


class TestResultStoreReDoSRegression:
    """Regresja F01 — wzorzec katastroficzny nie może zamrozić procesu."""

    async def test_filter_engine_is_re2_not_re(
        self, store: ResultStore, sample_results: list[ActSummaryOutput]
    ) -> None:
        """Asercja silnika na BEZPIECZNYM wzorcu, wykonana PRZED testem katastroficznym (U3b).

        Umyślnie osobny test i osobny, nieszkodliwy wzorzec ("Ustawa"): gdyby
        `compile_pattern` przestał być importowalny albo wewnętrznie wrócił do
        `re`, ten test albo od razu rzuci `AttributeError` przy wejściu w
        `patch(...)`, albo skończy się natychmiast — nie zawiesi joba CI, w
        przeciwieństwie do umieszczenia tej samej asercji w środku bloku `with`
        wokół wzorca katastroficznego (gdzie kod po `filter_results` nigdy nie
        zostałby osiągnięty przy regresji do `re`).
        """
        rs_id = await store.store(sample_results, "test", 5)

        captured: list[object] = []

        def _spy_compile_pattern(*args: object, **kwargs: object) -> object:
            compiled = compile_pattern(*args, **kwargs)
            captured.append(compiled)
            return compiled

        with patch(
            "law_scrapper_mcp.services.result_store.compile_pattern",
            side_effect=_spy_compile_pattern,
        ):
            await store.filter_results(rs_id, pattern="Ustawa")

        assert captured, "compile_pattern nie został wywołany"
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

        assert len(health) == 2  # dwa tytuły z "Zdrowia" — dopasowanie bez względu na wielkość liter
        assert len(wildcard) == 1
        # Fixture zawiera "Ustawa o podatku dochodowym", a wzorzec szuka "podatek".
        # Zero trafień jest zachowaniem identycznym ze stanem sprzed zmiany silnika.
        assert len(taxes) == 0

    @pytest.mark.timeout(5)
    async def test_alternation_matches_when_form_agrees(self, store: ResultStore) -> None:
        """Kontrola pozytywna dla wzorca z alternatywą — bez niej test powyżej jest ślepy."""
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
        """Granica: wzorzec o długości dokładnie równej limitowi nie jest odrzucany."""
        store = ResultStore(max_sets=5, ttl=60, max_pattern_length=64)
        rs_id = await store.store(sample_results, "test", 5)

        filtered, _ = await store.filter_results(rs_id, pattern="a" * 64)

        assert filtered == []


class TestResultStoreRecordCap:
    """D4 — odmowa wykonania zamiast wyniku częściowego."""

    async def test_oversized_set_is_refused(self, sample_results: list[ActSummaryOutput]) -> None:
        store = ResultStore(max_sets=5, ttl=60, max_records=3)
        rs_id = await store.store(sample_results, "test", 5)

        with pytest.raises(ResultSetTooLargeError) as exc_info:
            await store.filter_results(rs_id, pattern="Ustawa")

        assert exc_info.value.size == 5
        assert exc_info.value.limit == 3
        assert "Zawęź" in str(exc_info.value)

    async def test_refusal_applies_without_pattern_too(self, sample_results: list[ActSummaryOutput]) -> None:
        """Odmowa dotyczy wywołania, nie tylko ścieżki regex."""
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
