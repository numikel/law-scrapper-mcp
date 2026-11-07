"""`get_saos_judgment_details(load_content=...)` and what SAOS refusing a request does to the calls that follow.

Loading uses the recorded skeletons (see `saos_reading_testing`). The refusal tests for the portal links use the
recorded search items of `saos_testing`, where only the status of the detail answer is changed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
from saos_reading_testing import (
    EXTRA_REASONS_ID,
    LONE_ID,
    PAIR_NUMBER,
    REASONS_ID,
    SENTENCE_ID,
    MakeReading,
    ReadingHarness,
    pair_fake,
    pair_page,
    with_second_reasons,
)
from saos_testing import FakeSaos, HarnessFactory, corpus

from law_scrapper_mcp.context import AppContext
from law_scrapper_mcp.services.result_store import ResultStore
from law_scrapper_mcp_saos import service
from law_scrapper_mcp_saos.models import ReasoningStatus
from law_scrapper_mcp_saos.state import MODULE_NAME, SaosState

LABOUR_ID = corpus()[0]["id"]
TOOL_SEARCH = service.TOOL_SEARCH


def _context(harness: ReadingHarness) -> Any:
    state = SaosState(client=harness.client, module="law-scrapper-mcp-saos", version="0.1.0")
    return Mock(
        spec=AppContext, module_state={MODULE_NAME: state}, result_store=ResultStore(), document_store=harness.store
    )


@pytest.mark.asyncio
class TestLoadContent:
    async def test_without_the_flag_nothing_is_loaded_and_the_answer_is_the_old_one(
        self, make_reading: MakeReading
    ) -> None:
        harness = make_reading()

        response = await service.get_saos_judgment_details(_context(harness), source_id=SENTENCE_ID)

        assert response.data.loaded is None
        assert await harness.store.list_documents() == []
        assert harness.fake.detail_ids == [SENTENCE_ID]
        assert harness.fake.search_params == []
        assert len(response.hints) == 2  # the portal link and the pointer to the other record

    @pytest.mark.parametrize("flag", [True, "true", "True", "tak", "1"])
    async def test_the_flag_loads_the_text_whichever_way_it_is_spelled(
        self, make_reading: MakeReading, flag: bool | str
    ) -> None:
        harness = make_reading()

        response = await service.get_saos_judgment_details(_context(harness), source_id=REASONS_ID, load_content=flag)

        assert response.data.loaded is not None
        assert await harness.store.is_loaded(f"SAOS/{REASONS_ID}")

    @pytest.mark.parametrize("flag", [False, "false", "nie", "", None])
    async def test_a_false_flag_loads_nothing(self, make_reading: MakeReading, flag: bool | str | None) -> None:
        harness = make_reading()

        response = await service.get_saos_judgment_details(_context(harness), source_id=REASONS_ID, load_content=flag)

        assert response.data.loaded is None
        assert await harness.store.list_documents() == []

    async def test_a_value_that_is_no_flag_is_refused_before_any_call(self, make_reading: MakeReading) -> None:
        harness = make_reading()

        with pytest.raises(ValueError, match="load_content"):
            await service.get_saos_judgment_details(_context(harness), source_id=SENTENCE_ID, load_content="może")

        assert harness.fake.requests == 0

    async def test_the_metadata_comes_from_the_same_request_as_the_text(self, make_reading: MakeReading) -> None:
        harness = make_reading()

        response = await service.get_saos_judgment_details(_context(harness), source_id=SENTENCE_ID, load_content=True)

        assert harness.fake.detail_ids == [SENTENCE_ID, REASONS_ID]  # the sentence once, then its reasons record
        assert response.data.judgment.source_id == str(SENTENCE_ID)
        assert response.data.judgment.portal_url is not None
        assert response.data.division_type
        assert response.data.judgment.excerpt is None
        assert response.data.loaded is not None
        assert response.data.loaded.reasoning is ReasoningStatus.MERGED
        assert response.data.loaded.requests_made == 3


@pytest.mark.asyncio
class TestHintsAfterLoading:
    async def test_the_hints_say_how_to_read_and_search_the_document(self, make_reading: MakeReading) -> None:
        harness = make_reading()

        response = await service.get_saos_judgment_details(_context(harness), source_id=SENTENCE_ID, load_content=True)

        read, search = response.hints[0], response.hints[1]
        assert (read.tool, read.parameters) == ("read_act_content", {"eli": f"SAOS/{SENTENCE_ID}"})
        assert "nie ELI" in read.message
        assert (search.tool, search.parameters) == ("search_in_act", {"eli": f"SAOS/{SENTENCE_ID}"})

    async def test_the_hints_warn_about_markers_and_personal_data(self, make_reading: MakeReading) -> None:
        harness = make_reading()

        response = await service.get_saos_judgment_details(_context(harness), source_id=SENTENCE_ID, load_content=True)

        messages = " ".join(hint.message for hint in response.hints)
        assert "zanonimizowane" in messages
        assert "dane osobowe" in messages

    async def test_the_reasoning_note_is_a_hint_and_the_pointer_to_the_other_record_is_not(
        self, make_reading: MakeReading
    ) -> None:
        harness = make_reading()

        response = await service.get_saos_judgment_details(_context(harness), source_id=SENTENCE_ID, load_content=True)

        assert response.data.loaded is not None
        assert response.data.loaded.reasoning_note in [hint.message for hint in response.hints]
        assert all(hint.tool != TOOL_SEARCH for hint in response.hints)

    async def test_a_lone_sentence_says_the_source_has_no_reasoning(self, make_reading: MakeReading) -> None:
        harness = make_reading()

        response = await service.get_saos_judgment_details(_context(harness), source_id=LONE_ID, load_content=True)

        assert response.data.loaded is not None
        assert response.data.loaded.reasoning is ReasoningStatus.ABSENT
        assert any(hint.message.startswith("Brak uzasadnienia w źródle") for hint in response.hints)

    async def test_a_reasons_record_points_to_its_sentence_by_signature(self, make_reading: MakeReading) -> None:
        harness = make_reading()

        response = await service.get_saos_judgment_details(_context(harness), source_id=REASONS_ID, load_content=True)

        pointer = next(hint for hint in response.hints if hint.tool == TOOL_SEARCH)
        assert pointer.parameters == {"signature": PAIR_NUMBER}

    async def test_two_reasons_records_get_a_load_hint_each(self, make_reading: MakeReading) -> None:
        fake = pair_fake()
        fake.searches[PAIR_NUMBER] = with_second_reasons(pair_page())
        harness = make_reading(fake)

        response = await service.get_saos_judgment_details(_context(harness), source_id=SENTENCE_ID, load_content=True)

        loads = [hint for hint in response.hints if hint.tool == service.TOOL_DETAILS]
        assert [hint.parameters for hint in loads] == [
            {"source_id": str(REASONS_ID), "load_content": True},
            {"source_id": str(EXTRA_REASONS_ID), "load_content": True},
        ]


@pytest.mark.asyncio
class TestARefusalEndsTheCallsThatFollow:
    """After SAOS says no (401, 403, 451) nothing more is asked in the same call, and the refusal is not retried."""

    @pytest.mark.parametrize("status", [401, 403, 451])
    async def test_a_refused_search_stops_the_load_after_two_calls(
        self, make_reading: MakeReading, status: int
    ) -> None:
        harness = make_reading(pair_fake(refused_search=True, refusal_status=status))

        loaded = await harness.service.load(SENTENCE_ID)

        assert loaded.reasoning is ReasoningStatus.NOT_ASSESSED
        assert loaded.requests_made == 2
        assert harness.fake.detail_ids == [SENTENCE_ID]  # the reasons record was not asked for
        assert len(harness.fake.search_params) == 1  # and the search was not repeated
        assert await harness.store.is_loaded(loaded.document_id)

    @pytest.mark.parametrize("status", [401, 403, 451])
    async def test_a_refused_reasons_record_is_the_last_call(self, make_reading: MakeReading, status: int) -> None:
        harness = make_reading(pair_fake(refused_details=[REASONS_ID], refusal_status=status))

        loaded = await harness.service.load(SENTENCE_ID)

        assert loaded.reasoning is ReasoningStatus.NOT_ASSESSED
        assert loaded.requests_made == 3
        assert harness.fake.detail_ids == [SENTENCE_ID, REASONS_ID]  # asked once, not retried
        assert len(harness.fake.search_params) == 1

    async def test_the_refusal_helper_knows_the_statuses_and_only_them(self) -> None:
        from law_scrapper_mcp.client.exceptions import ApiUnavailableError, SejmApiError

        for status in (401, 403, 429, 451):
            assert service.is_refusal(SejmApiError("x", status_code=status)) is True
        for status in (400, 404, 500, None):
            assert service.is_refusal(SejmApiError("x", status_code=status)) is False
        assert service.is_refusal(ApiUnavailableError("x", status_code=503)) is False
        assert service.is_refusal(ValueError("x")) is False

    @pytest.mark.parametrize("status", [401, 403, 451])
    async def test_one_refused_detail_sends_no_further_detail_request(
        self, make_harness: HarnessFactory, status: int
    ) -> None:
        fake = FakeSaos(failing_details={LABOUR_ID}, status=status)
        harness = make_harness(fake)

        response = await harness.service.search(limit=5, include_portal_links=True)

        links = response.data.portal_links
        assert links is not None
        assert fake.detail_ids == [LABOUR_ID]
        assert links.stopped_after_outage is True
        assert links.resolved == 0
        assert links.failed == links.requested == 5

    async def test_a_missing_judgment_is_not_a_refusal_and_the_loop_goes_on(self, make_harness: HarnessFactory) -> None:
        """Control: a 404 costs one result its link, and the next ones are still asked for."""
        fake = FakeSaos(missing_details={LABOUR_ID})
        harness = make_harness(fake)

        response = await harness.service.search(limit=5, include_portal_links=True)

        links = response.data.portal_links
        assert links is not None
        assert len(fake.detail_ids) == 5
        assert links.stopped_after_outage is False
        assert links.failed == 1 and links.resolved == 4
