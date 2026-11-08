"""Request building, validation and hints of the SAOS search service."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
from saos_testing import TODAY, FakeSaos, Harness, HarnessFactory, corpus, supreme_chamber_items

from law_scrapper_mcp.client.exceptions import SejmApiError
from law_scrapper_mcp.models.tool_outputs import Hint
from law_scrapper_mcp.services.coverage import FALLBACK_NOTE, CoverageRegistry, SourceCoverage
from law_scrapper_mcp_saos import service as service_module
from law_scrapper_mcp_saos.coverage import ADMINISTRATIVE_MESSAGE, COVERAGE_KEY, SaosCoverageProvider
from law_scrapper_mcp_saos.service import (
    LABOUR_DIVISION_PATTERN,
    TOOL_SEARCH,
    SaosSearchService,
    build_query,
)

pytestmark = pytest.mark.asyncio

CORPUS = corpus()


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _hint_for(hints: list[Hint], tool: str) -> Hint:
    return next(hint for hint in hints if hint.tool == tool)


class TestRequestParameters:
    async def test_every_filter_reaches_saos_under_its_own_name(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        await harness.service.search(
            phrase="wypadek przy pracy",
            signature="VIII U 2924/25",
            date_from="2020-01-01",
            date_to="2022-12-31",
            court_type="common",
            judgment_type="Wyrok",
            limit=5,
            offset=10,
        )

        assert harness.fake.calls == [
            {
                "pageSize": "5",
                "pageNumber": "2",
                "all": "wypadek przy pracy",
                "caseNumber": "VIII U 2924/25",
                "courtType": "COMMON",
                "judgmentTypes": "SENTENCE",
                "judgmentDateFrom": "2020-01-01",
                "judgmentDateTo": "2022-12-31",
                "sortingField": "JUDGMENT_DATE",
                "sortingDirection": "DESC",
            }
        ]

    async def test_a_search_without_filters_still_sends_the_upper_date_bound_and_the_sort(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness()

        await harness.service.search()

        assert harness.fake.calls[0] == {
            "pageSize": "20",
            "pageNumber": "0",
            "judgmentDateTo": TODAY.isoformat(),
            "sortingField": "JUDGMENT_DATE",
            "sortingDirection": "DESC",
        }

    async def test_the_parameter_order_is_fixed_so_equal_searches_share_a_cached_page(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness()

        await harness.service.search(phrase="a", court_type="COMMON", date_from="2020-01-01")
        await harness.service.search(date_from="2020-01-01", court_type="COMMON", phrase="a")

        assert len(harness.fake.urls) == 1
        keys = [key for key, _ in parse_qsl(urlsplit(harness.fake.urls[0]).query)]
        assert keys == [
            "pageSize",
            "pageNumber",
            "all",
            "courtType",
            "judgmentDateFrom",
            "judgmentDateTo",
            "sortingField",
            "sortingDirection",
        ]

    @pytest.mark.parametrize(
        ("label", "saos_name"),
        [
            ("Wyrok", "SENTENCE"),
            ("postanowienie", "DECISION"),
            ("UCHWAŁA", "RESOLUTION"),
            ("Zarządzenie", "REGULATION"),
            ("Uzasadnienie", "REASONS"),
            ("sentence", "SENTENCE"),
        ],
    )
    async def test_judgment_type_accepts_the_polish_label_and_the_saos_name(
        self, make_harness: HarnessFactory, label: str, saos_name: str
    ) -> None:
        harness = make_harness()

        await harness.service.search(judgment_type=label)

        assert harness.fake.calls[0]["judgmentTypes"] == saos_name

    async def test_blank_filters_are_ignored(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        await harness.service.search(phrase="  ", signature="", court_type=" ", judgment_type="", date_from=" ")

        assert set(harness.fake.calls[0]) == {
            "pageSize",
            "pageNumber",
            "judgmentDateTo",
            "sortingField",
            "sortingDirection",
        }

    async def test_the_phrase_is_trimmed(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        await harness.service.search(phrase="  mobbing  ")

        assert harness.fake.calls[0]["all"] == "mobbing"


class TestDates:
    async def test_a_future_date_to_is_cut_to_today_and_the_summary_says_so(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        response = await harness.service.search(date_to="2999-12-31")

        assert harness.fake.calls[0]["judgmentDateTo"] == TODAY.isoformat()
        assert "ograniczone do dziś" in response.data.query_summary

    async def test_a_past_date_to_is_kept(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        response = await harness.service.search(date_to="2020-05-17")

        assert harness.fake.calls[0]["judgmentDateTo"] == "2020-05-17"
        assert "ograniczone" not in response.data.query_summary

    async def test_today_comes_from_the_injected_clock(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(today=date(2030, 1, 2))

        await harness.service.search()

        assert harness.fake.calls[0]["judgmentDateTo"] == "2030-01-02"

    async def test_the_real_clock_is_used_by_default(
        self, make_harness: HarnessFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        harness = make_harness()
        monkeypatch.setattr(service_module, "current_date", lambda: date(2031, 3, 4))
        service = SaosSearchService(harness.service._client, harness.store)

        await service.search()

        assert harness.fake.calls[0]["judgmentDateTo"] == "2031-03-04"

    async def test_date_from_after_today_is_refused_before_any_request(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        with pytest.raises(ValueError, match="date_from.*dzisiejsza data"):
            await harness.service.search(date_from="2026-11-04")

        assert harness.fake.calls == []

    async def test_date_from_today_is_allowed(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        await harness.service.search(date_from=TODAY.isoformat())

        assert harness.fake.calls[0]["judgmentDateFrom"] == TODAY.isoformat()

    async def test_date_from_after_date_to_is_refused(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        with pytest.raises(ValueError, match="date_from"):
            await harness.service.search(date_from="2021-01-01", date_to="2020-01-01")

        assert harness.fake.calls == []

    @pytest.mark.parametrize(
        "value", ["2020/01/01", "20200101", "1.1.2020", "2020-13-01", "2020-02-30", "abc", "2020-1-1"]
    )
    async def test_a_malformed_date_is_refused(self, make_harness: HarnessFactory, value: str) -> None:
        harness = make_harness()

        with pytest.raises(ValueError, match="date_to"):
            await harness.service.search(date_to=value)

        assert harness.fake.calls == []


class TestValidation:
    async def test_an_unknown_court_type_lists_the_valid_ones(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        with pytest.raises(ValueError) as excinfo:
            await harness.service.search(court_type="APPEAL")

        for name in ("COMMON", "SUPREME", "CONSTITUTIONAL_TRIBUNAL", "NATIONAL_APPEAL_CHAMBER", "ADMINISTRATIVE"):
            assert name in str(excinfo.value)
        assert harness.fake.calls == []

    async def test_an_unknown_judgment_type_lists_the_valid_ones(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        with pytest.raises(ValueError) as excinfo:
            await harness.service.search(judgment_type="Wyrok końcowy")

        for label in ("Wyrok", "Postanowienie", "Uchwała", "Zarządzenie", "Uzasadnienie"):
            assert label in str(excinfo.value)

    @pytest.mark.parametrize(("name", "limit"), [("phrase", 500), ("signature", 100)])
    async def test_text_filters_are_bounded_and_the_bound_is_inclusive(
        self, make_harness: HarnessFactory, name: str, limit: int
    ) -> None:
        harness = make_harness()

        await harness.service.search(**{name: "x" * limit})
        with pytest.raises(ValueError, match=name):
            await harness.service.search(**{name: "x" * (limit + 1)})

        assert len(harness.fake.calls) == 1

    async def test_build_query_is_the_single_place_that_validates(self) -> None:
        query = build_query(
            phrase=None,
            signature=None,
            date_from=None,
            date_to=None,
            court_type="supreme",
            judgment_type=None,
            today=TODAY,
        )

        assert query.court_type == "SUPREME"
        assert query.date_to == TODAY


class TestHints:
    async def test_next_page_hint_repeats_the_search_with_the_next_offset(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        response = await harness.service.search(
            phrase="mobbing", court_type="common", judgment_type="wyrok", limit=5, offset=5
        )

        hint = _hint_for(response.hints, TOOL_SEARCH)
        assert response.data.page_info.next_offset == 10
        assert hint.parameters == {
            "phrase": "mobbing",
            "date_to": TODAY.isoformat(),
            "court_type": "COMMON",
            "judgment_type": "Wyrok",
            "limit": 5,
            "offset": 10,
        }

    async def test_no_next_page_hint_on_the_last_page(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos(CORPUS[:12]))

        response = await harness.service.search(limit=10, offset=10)

        assert response.data.page_info.next_offset is None
        assert all(hint.parameters is None or "offset" not in hint.parameters for hint in response.hints)

    async def test_labour_narrowing_hint_carries_the_shared_pattern_and_its_variant(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness()

        response = await harness.service.search()

        hint = _hint_for(response.hints, "filter_results")
        assert hint.parameters == {
            "result_set_id": response.data.result_set_id,
            "pattern": LABOUR_DIVISION_PATTERN,
            "field": "division_code",
        }
        assert LABOUR_DIVISION_PATTERN == "(15|18|21)$"
        assert "(15|21)$" in hint.message
        assert "pattern='Pracy'" in hint.message

    @pytest.mark.parametrize("court_type", ["CONSTITUTIONAL_TRIBUNAL", "NATIONAL_APPEAL_CHAMBER"])
    async def test_no_narrowing_hint_for_courts_without_a_labour_classification(
        self, make_harness: HarnessFactory, court_type: str
    ) -> None:
        harness = make_harness()

        response = await harness.service.search(court_type=court_type)

        assert all(hint.tool != "filter_results" for hint in response.hints)

    async def test_the_supreme_court_hint_narrows_by_chamber_name_not_by_division_code(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness()

        response = await harness.service.search(court_type="SUPREME")

        hint = _hint_for(response.hints, "filter_results")
        assert hint.parameters == {
            "result_set_id": response.data.result_set_id,
            "pattern": "Izba Pracy",
            "field": "division",
        }
        assert all((h.parameters or {}).get("field") != "division_code" for h in response.hints)

    async def test_a_window_set_says_that_filtering_covers_the_window_only(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        response = await harness.service.search(limit=5, offset=5)

        assert any("tylko to okno" in hint.message for hint in response.hints)

    async def test_a_complete_set_does_not(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos(CORPUS[:3]))

        response = await harness.service.search()

        assert all("tylko to okno" not in hint.message for hint in response.hints)

    async def test_sentence_and_reasons_pairing_hint_points_at_the_signature(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness()

        response = await harness.service.search(limit=5)

        hint = next(h for h in response.hints if h.tool == TOOL_SEARCH and h.parameters and "signature" in h.parameters)
        assert hint.parameters == {"signature": response.data.results[0].signature}
        assert "dwa osobne rekordy" in hint.message

    async def test_the_old_static_coverage_sentence_is_gone_from_the_first_page(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness()

        first = await harness.service.search(limit=5, offset=0)

        assert all("Zasięg SAOS" not in hint.message for hint in first.hints)


class _StubProvider:
    """A provider that answers with fixed coverage (or fails), and counts its measurements."""

    key = COVERAGE_KEY
    source = "saos"

    def __init__(self, coverage: SourceCoverage, *, failing: bool = False) -> None:
        self._coverage = coverage
        self._failing = failing
        self.measurements = 0

    async def measure(self, today: date) -> SourceCoverage:
        self.measurements += 1
        if self._failing:
            raise SejmApiError("SAOS nie odpowiedział.", source="SAOS")
        return self._coverage

    def fallback(self) -> SourceCoverage:
        return replace(self._coverage, measured_on=date(2026, 11, 2), origin="fallback")


class _ExplodingRegistry(CoverageRegistry):
    async def get(self, key: str) -> SourceCoverage | None:
        raise RuntimeError("the registry is broken")


def _recorded(harness: Harness) -> SourceCoverage:
    """The recorded figures of 2026-11-02 presented as a measurement taken on the test's `TODAY`."""
    return replace(SaosCoverageProvider(harness.client).fallback(), measured_on=TODAY, origin="measured")


def _gap_service(harness: Harness, provider: _StubProvider | None = None) -> tuple[SaosSearchService, _StubProvider]:
    registry = CoverageRegistry(today=lambda: TODAY)
    stub = provider or _StubProvider(_recorded(harness))
    registry.register(stub)
    return SaosSearchService(harness.client, harness.store, today=lambda: TODAY, coverage=registry), stub


def _gap_hints(hints: list[Hint]) -> list[str]:
    return [h.message for h in hints if "w SAOS sięg" in h.message or h.message.startswith(ADMINISTRATIVE_MESSAGE)]


SUPREME_GAP = (
    "Sąd Najwyższy w SAOS sięga do 2016-06-22 (pomiar z 2026-11-03); orzeczeń z późniejszych dat w tym źródle "
    "nie ma. Brak wyniku w SAOS nie dowodzi, że orzeczenie nie istnieje."
)


class TestCoverageGapHint:
    async def test_the_supreme_court_without_dates_reaches_today_and_gets_the_gap(
        self, make_harness: HarnessFactory
    ) -> None:
        service, _ = _gap_service(make_harness())

        response = await service.search(court_type="SUPREME", limit=5)

        assert _gap_hints(response.hints) == [SUPREME_GAP]

    async def test_the_supreme_court_from_2015_gets_the_gap_because_the_upper_bound_is_today(
        self, make_harness: HarnessFactory
    ) -> None:
        service, _ = _gap_service(make_harness())

        response = await service.search(court_type="SUPREME", date_from="2015-01-01", limit=5)

        assert _gap_hints(response.hints) == [SUPREME_GAP]

    async def test_thirty_days_past_the_newest_is_no_gap_and_thirty_one_is(self, make_harness: HarnessFactory) -> None:
        service, _ = _gap_service(make_harness())

        at_limit = await service.search(court_type="SUPREME", date_to="2016-07-22", limit=5)
        past_limit = await service.search(court_type="SUPREME", date_to="2016-07-23", limit=5)

        assert _gap_hints(at_limit.hints) == []
        assert _gap_hints(past_limit.hints) == [SUPREME_GAP]

    async def test_a_search_that_ends_before_the_newest_judgment_gets_no_gap(
        self, make_harness: HarnessFactory
    ) -> None:
        service, _ = _gap_service(make_harness())

        response = await service.search(court_type="CONSTITUTIONAL_TRIBUNAL", date_to="2015-06-30", limit=5)

        assert _gap_hints(response.hints) == []

    async def test_common_courts_are_current_so_there_is_no_gap(self, make_harness: HarnessFactory) -> None:
        service, _ = _gap_service(make_harness())

        response = await service.search(court_type="COMMON", limit=5)

        assert _gap_hints(response.hints) == []

    async def test_common_courts_use_the_plural_verb_once_the_search_outruns_them(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness()
        recorded = _recorded(harness)
        segments = tuple(
            replace(segment, newest=date(2026, 9, 1)) if segment.key == "COMMON" else segment
            for segment in recorded.segments
        )
        service, _ = _gap_service(harness, _StubProvider(replace(recorded, segments=segments)))

        response = await service.search(court_type="COMMON", limit=5)

        assert _gap_hints(response.hints) == [
            "Sądy powszechne w SAOS sięgają do 2026-09-01 (pomiar z 2026-11-03); orzeczeń z późniejszych dat "
            "w tym źródle nie ma. Brak wyniku w SAOS nie dowodzi, że orzeczenie nie istnieje."
        ]

    async def test_a_search_without_a_court_type_gets_no_gap_and_no_measurement(
        self, make_harness: HarnessFactory
    ) -> None:
        service, stub = _gap_service(make_harness())

        response = await service.search(limit=5)

        assert _gap_hints(response.hints) == []
        assert stub.measurements == 0

    async def test_administrative_courts_get_the_statement_while_the_measurement_counts_none(
        self, make_harness: HarnessFactory
    ) -> None:
        service, stub = _gap_service(make_harness())

        response = await service.search(court_type="ADMINISTRATIVE", limit=5)

        assert _gap_hints(response.hints) == ["SAOS nie zawiera orzeczeń sądów administracyjnych."]
        assert stub.measurements == 1, "the statement rests on the measurement, not on an assumption"

    async def test_administrative_courts_lose_the_statement_once_saos_counts_some(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness()
        recorded = _recorded(harness)
        segments = tuple(
            replace(segment, count=12, newest=date(2026, 11, 1)) if segment.key == "ADMINISTRATIVE" else segment
            for segment in recorded.segments
        )
        service, _ = _gap_service(harness, _StubProvider(replace(recorded, segments=segments)))

        response = await service.search(court_type="ADMINISTRATIVE", limit=5)

        assert _gap_hints(response.hints) == [], "a current, non-empty court type has no gap"

    async def test_the_administrative_statement_from_a_fallback_carries_the_note(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness()
        service, _ = _gap_service(harness, _StubProvider(_recorded(harness), failing=True))

        response = await service.search(court_type="ADMINISTRATIVE", limit=5)

        assert _gap_hints(response.hints) == [f"SAOS nie zawiera orzeczeń sądów administracyjnych. {FALLBACK_NOTE}"]

    async def test_the_gap_is_on_the_first_page_only(self, make_harness: HarnessFactory) -> None:
        service, _ = _gap_service(make_harness())

        first = await service.search(court_type="SUPREME", limit=5, offset=0)
        later = await service.search(court_type="SUPREME", limit=5, offset=5)

        assert _gap_hints(first.hints) == [SUPREME_GAP]
        assert _gap_hints(later.hints) == []

    async def test_two_searches_share_one_measurement_because_the_service_reads_through_the_registry(
        self, make_harness: HarnessFactory
    ) -> None:
        service, stub = _gap_service(make_harness())

        await service.search(court_type="SUPREME", limit=5)
        await service.search(court_type="CONSTITUTIONAL_TRIBUNAL", limit=5)

        assert stub.measurements == 1

    async def test_a_labour_scan_of_the_supreme_court_gets_the_gap_on_its_first_window_only(
        self, make_harness: HarnessFactory
    ) -> None:
        service, _ = _gap_service(make_harness(FakeSaos(supreme_chamber_items())))

        first = await service.search(court_type="SUPREME", labour_only=True, limit=5)
        later = await service.search(court_type="SUPREME", labour_only=True, limit=5, offset=100)

        assert first.data.scan is not None and first.data.scan.scanned_from == 0
        assert _gap_hints(first.hints) == [SUPREME_GAP]
        assert _gap_hints(later.hints) == []

    async def test_a_fallback_value_carries_the_note_and_its_own_measurement_day(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness()
        service, _ = _gap_service(harness, _StubProvider(_recorded(harness), failing=True))

        response = await service.search(court_type="SUPREME", limit=5)

        assert _gap_hints(response.hints) == [
            SUPREME_GAP.replace("pomiar z 2026-11-03", "pomiar z 2026-11-02") + f" {FALLBACK_NOTE}"
        ]

    async def test_a_failing_coverage_lookup_never_costs_the_search_result(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()
        service = SaosSearchService(
            harness.client, harness.store, today=lambda: TODAY, coverage=_ExplodingRegistry(today=lambda: TODAY)
        )

        response = await service.search(court_type="SUPREME", limit=5)

        assert response.data.returned_count == 5
        assert _gap_hints(response.hints) == []

    async def test_a_service_without_a_registry_gives_the_search_and_no_gap(self, make_harness: HarnessFactory) -> None:
        response = await make_harness().service.search(court_type="SUPREME", limit=5)

        assert response.data.returned_count == 5
        assert _gap_hints(response.hints) == []


class TestFilterLimitHint:
    async def test_a_set_larger_than_filter_results_accepts_comes_with_the_remedy(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(filter_max_records=3)

        response = await harness.service.search(limit=5, offset=5)

        hint = next(h for h in response.hints if "filter_results przyjmuje najwyżej 3" in h.message)
        assert hint.tool == TOOL_SEARCH
        assert hint.parameters is not None
        assert (hint.parameters["limit"], hint.parameters["offset"]) == (3, 5)

    async def test_a_set_within_the_limit_has_no_such_hint(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(filter_max_records=5)

        response = await harness.service.search(limit=5)

        assert all("przyjmuje najwyżej" not in h.message for h in response.hints)

    async def test_the_default_comes_from_the_core_setting_and_covers_the_largest_page(
        self, make_harness: HarnessFactory
    ) -> None:
        from law_scrapper_mcp.config import settings

        harness = make_harness()

        response = await harness.service.search(limit=100)

        assert settings.effective_filter_max_records >= response.data.returned_count
        assert all("przyjmuje najwyżej" not in h.message for h in response.hints)

    async def test_the_oversized_set_really_is_refused_by_the_core_filter(self, make_harness: HarnessFactory) -> None:
        from law_scrapper_mcp.services.result_store import ResultSetTooLargeError, ResultStore

        harness = make_harness()
        harness.store.max_records = 3
        assert isinstance(harness.store, ResultStore)

        response = await harness.service.search(limit=5)

        with pytest.raises(ResultSetTooLargeError):
            await harness.store.filter_and_store(response.data.result_set_id or "", pattern="x")


class TestPrivacyOfTheOutput:
    async def test_no_judge_name_or_role_leaves_the_module(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        response = await harness.service.search(limit=100)
        dumped = response.model_dump_json()

        assert response.data.returned_count == len(CORPUS)
        assert "[redacted]" not in dumped
        assert "PRESIDING_JUDGE" not in dumped
        assert "judges" not in dumped


class TestUpstreamAnswers:
    async def test_an_answer_that_is_not_a_search_page_is_an_upstream_error_naming_the_source(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos())
        assert harness.fake.route is not None
        harness.fake.route.mock(return_value=httpx.Response(200, json=["not", "a", "page"]))

        with pytest.raises(SejmApiError) as excinfo:
            await harness.service.search()

        assert excinfo.value.source == "SAOS"
