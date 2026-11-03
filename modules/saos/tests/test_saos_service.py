"""Request building, validation and hints of the SAOS search service."""

from __future__ import annotations

from datetime import date
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
from saos_testing import TODAY, FakeSaos, HarnessFactory, corpus

from law_scrapper_mcp.client.exceptions import SejmApiError
from law_scrapper_mcp.models.tool_outputs import Hint
from law_scrapper_mcp_saos import service as service_module
from law_scrapper_mcp_saos.service import (
    COVERAGE_SENTENCE,
    LABOUR_DIVISION_PATTERN,
    TOOL_SEARCH,
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
        from law_scrapper_mcp_saos.service import SaosSearchService

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

    @pytest.mark.parametrize("court_type", ["SUPREME", "CONSTITUTIONAL_TRIBUNAL", "NATIONAL_APPEAL_CHAMBER"])
    async def test_no_division_code_hint_for_courts_that_have_none(
        self, make_harness: HarnessFactory, court_type: str
    ) -> None:
        harness = make_harness()

        response = await harness.service.search(court_type=court_type)

        assert all(hint.tool != "filter_results" for hint in response.hints)

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

    async def test_the_coverage_sentence_is_on_the_first_page_only(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        first = await harness.service.search(limit=5, offset=0)
        later = await harness.service.search(limit=5, offset=5)

        assert COVERAGE_SENTENCE in [hint.message for hint in first.hints]
        assert COVERAGE_SENTENCE not in [hint.message for hint in later.hints]


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
