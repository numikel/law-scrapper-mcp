"""Paging over the SAOS list: any offset, the request budget, the end of the list, the seam of two pages."""

from __future__ import annotations

from typing import Any

import pytest
from saos_testing import FakeSaos, HarnessFactory, corpus

from law_scrapper_mcp.client.exceptions import ApiUnavailableError
from law_scrapper_mcp.models.tool_outputs import DocumentKind, SetScope

pytestmark = pytest.mark.asyncio

CORPUS = corpus()


def _ids(items: list[dict[str, Any]]) -> list[str]:
    return [str(item["id"]) for item in items]


async def test_the_recorded_corpus_is_big_enough_for_the_scenarios_below() -> None:
    assert len(CORPUS) >= 40
    assert len({item["id"] for item in CORPUS}) == len(CORPUS)


class TestAnyOffset:
    async def test_an_aligned_offset_costs_one_request_for_that_page(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        response = await harness.service.search(limit=5, offset=10)

        assert harness.fake.pages == [2]
        assert harness.fake.calls[0]["pageSize"] == "5"
        assert [r.source_id for r in response.data.results] == _ids(CORPUS[10:15])
        assert response.data.total_count == len(CORPUS)
        assert response.data.page_info.offset == 10

    async def test_a_misaligned_offset_reads_two_pages_and_cuts_the_window(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        response = await harness.service.search(limit=5, offset=7)

        assert harness.fake.pages == [1, 2]
        assert [r.source_id for r in response.data.results] == _ids(CORPUS[7:12])
        assert response.data.page_info.next_offset == 12

    async def test_the_second_page_is_skipped_when_the_first_one_already_ended_the_list(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(CORPUS[:23]))

        response = await harness.service.search(limit=10, offset=21)

        assert harness.fake.pages == [2]
        assert [r.source_id for r in response.data.results] == _ids(CORPUS[21:23])
        assert response.data.total_count == 23
        assert response.data.page_info.next_offset is None

    async def test_the_second_page_is_requested_when_the_list_goes_on(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos(CORPUS[:20]))

        response = await harness.service.search(limit=10, offset=5)

        assert harness.fake.pages == [0, 1]
        assert [r.source_id for r in response.data.results] == _ids(CORPUS[5:15])

    async def test_the_second_page_is_skipped_when_the_first_one_is_full_and_last(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(CORPUS[:10]))

        response = await harness.service.search(limit=10, offset=5)

        assert harness.fake.pages == [0]
        assert [r.source_id for r in response.data.results] == _ids(CORPUS[5:10])

    async def test_an_offset_past_the_end_is_an_empty_page_with_the_total_and_a_hint(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(CORPUS[:12]))

        response = await harness.service.search(limit=5, offset=50)

        assert harness.fake.pages == [10]
        assert response.data.results == []
        assert response.data.result_set_id is None
        assert response.data.total_count == 12
        assert any("poza wynikami" in hint.message for hint in response.hints)

    async def test_an_offset_equal_to_the_total_is_past_the_end(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos(CORPUS[:10]))

        response = await harness.service.search(limit=5, offset=10)

        assert harness.fake.pages == [2]
        assert response.data.returned_count == 0

    async def test_no_results_at_all_says_so(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos([]))

        response = await harness.service.search(phrase="nic")

        assert response.data.total_count == 0
        assert any("Brak orzeczeń" in hint.message for hint in response.hints)


class TestLimit:
    @pytest.mark.parametrize("requested", [101, 500, "101", 10**6])
    async def test_a_limit_above_100_is_clamped_before_the_wire(
        self, make_harness: HarnessFactory, requested: str | int
    ) -> None:
        harness = make_harness()

        response = await harness.service.search(limit=requested)

        assert harness.fake.calls[0]["pageSize"] == "100"
        assert response.data.page_info.limit == 100

    async def test_the_default_limit_is_20_and_a_string_limit_is_accepted(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        default = await harness.service.search()
        as_text = await harness.service.search(limit="7", offset="14")

        assert default.data.returned_count == 20
        assert as_text.data.returned_count == 7
        assert [call["pageSize"] for call in harness.fake.calls] == ["20", "7"]

    @pytest.mark.parametrize("limit", [0, -1, "abc", "1.5"])
    async def test_an_invalid_limit_is_refused_before_any_request(
        self, make_harness: HarnessFactory, limit: str | int
    ) -> None:
        harness = make_harness()

        with pytest.raises(ValueError, match="limit"):
            await harness.service.search(limit=limit)

        assert harness.fake.calls == []

    @pytest.mark.parametrize("offset", [-1, "abc", "-3"])
    async def test_an_invalid_offset_is_refused_before_any_request(
        self, make_harness: HarnessFactory, offset: str | int
    ) -> None:
        harness = make_harness()

        with pytest.raises(ValueError, match="offset"):
            await harness.service.search(offset=offset)

        assert harness.fake.calls == []


LIMITS = (1, 2, 3, 5, 7, 10, 20, 100)
OFFSETS = (0, 1, 2, 3, 5, 7, 9, 10, 13, 20, 25, 37, 60, 500)


class TestRequestBudget:
    @pytest.mark.parametrize("limit", LIMITS)
    @pytest.mark.parametrize("offset", OFFSETS)
    async def test_never_more_than_two_requests_and_always_the_right_window(
        self, make_harness: HarnessFactory, limit: int, offset: int
    ) -> None:
        harness = make_harness()

        response = await harness.service.search(limit=limit, offset=offset)

        assert 1 <= len(harness.fake.calls) <= 2
        if offset % limit == 0:
            assert len(harness.fake.calls) == 1
        assert all(int(call["pageSize"]) == limit for call in harness.fake.calls)
        assert [r.source_id for r in response.data.results] == _ids(CORPUS[offset : offset + limit])


class TestSeamOfTwoPages:
    async def test_a_record_inserted_between_the_two_requests_is_not_shown_twice(
        self, make_harness: HarnessFactory
    ) -> None:
        # The second page starts with the record that closed the first: what SAOS would answer if one
        # record had been added at the top between the two requests.
        items = list(CORPUS[:10])
        items[5] = items[4]
        harness = make_harness(FakeSaos(items))

        response = await harness.service.search(limit=5, offset=3)

        ids = [r.source_id for r in response.data.results]
        assert harness.fake.pages == [0, 1]
        assert len(ids) == len(set(ids))
        assert ids == _ids([items[3], items[4], items[6], items[7], items[8]])


class TestCache:
    async def test_pages_read_for_aligned_calls_serve_a_later_misaligned_call(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness()

        await harness.service.search(limit=5, offset=0)
        await harness.service.search(limit=5, offset=5)
        misaligned = await harness.service.search(limit=5, offset=3)

        assert harness.fake.pages == [0, 1]
        assert [r.source_id for r in misaligned.data.results] == _ids(CORPUS[3:8])


class TestStoredSet:
    async def test_a_window_is_stored_as_a_judgment_set_with_its_position(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        response = await harness.service.search(limit=5, offset=5)

        stored = await harness.store.get(response.data.result_set_id or "")
        assert stored is not None
        assert stored.document_kind is DocumentKind.JUDGMENT
        assert stored.window_offset == 5
        assert stored.total_count == len(CORPUS)
        assert [j.source_id for j in stored.results] == _ids(CORPUS[5:10])  # type: ignore[union-attr]
        scope = response.data.result_set_scope
        assert scope is not None and scope.scope is SetScope.PAGE
        assert scope.corpus_count == len(CORPUS)

    async def test_a_result_that_fits_one_page_is_a_complete_set(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos(CORPUS[:3]))

        response = await harness.service.search()

        scope = response.data.result_set_scope
        assert scope is not None and scope.scope is SetScope.COMPLETE

    async def test_an_empty_page_stores_nothing(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos([]))

        await harness.service.search()

        assert await harness.store.list_sets() == []

    async def test_the_stored_window_can_be_filtered_with_the_shared_tool_logic(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness()
        response = await harness.service.search(limit=20, offset=0)

        output = await harness.store.filter_and_store(
            response.data.result_set_id or "", pattern="(15|18|21)$", field="division_code"
        )

        expected = [
            r.source_id for r in response.data.results if r.division_code and r.division_code[-2:] in ("15", "18", "21")
        ]
        assert [j.source_id for j in output.judgments] == expected
        assert expected, "the first recorded page is a labour division"


class TestFailures:
    async def test_the_first_page_failing_fails_the_call_and_names_the_source(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(failing_pages={0}))

        with pytest.raises(ApiUnavailableError) as excinfo:
            await harness.service.search(limit=5)

        assert excinfo.value.source == "SAOS"
        assert await harness.store.list_sets() == []

    async def test_the_second_page_failing_fails_the_call_instead_of_returning_half_a_window(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(failing_pages={2}))

        with pytest.raises(ApiUnavailableError):
            await harness.service.search(limit=5, offset=7)

        assert await harness.store.list_sets() == []
