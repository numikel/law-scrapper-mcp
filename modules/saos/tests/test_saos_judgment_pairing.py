"""Where the reasoning of a judgment is: in its record, in a separate SAOS record, nowhere, or unknown.

Every test states the status and the number of calls to SAOS, because the number is part of the contract: a load
sends at most three (the detail, a search by case number, the detail of the reasons record).
"""

from __future__ import annotations

import pytest
from saos_reading_testing import (
    EXTRA_REASONS_ID,
    LONE_ID,
    LONE_NUMBER,
    NESTED_ID,
    PAIR_NUMBER,
    REASONS_ID,
    SENTENCE_ID,
    SUPREME_ID,
    MakeReading,
    cut_short,
    pair_fake,
    pair_page,
    skeleton,
    with_reasons_of_another_court,
    with_second_reasons,
)

from law_scrapper_mcp_saos import reading, service
from law_scrapper_mcp_saos.models import ReasoningStatus


async def toc_titles(harness, key: str) -> list[str]:  # type: ignore[no-untyped-def]
    return [section.title for section in await harness.store.get_toc(key)]


async def stored_text(harness, key: str) -> str:  # type: ignore[no-untyped-def]
    return str(harness.store._store[key].markdown)  # noqa: SLF001 - the stored text is what a read returns


@pytest.mark.asyncio
class TestASentenceWithItsReasons:
    async def test_the_reasons_record_is_found_by_case_number_and_joined(self, make_reading: MakeReading) -> None:
        harness = make_reading()

        loaded = await harness.service.load(SENTENCE_ID)

        assert loaded.reasoning is ReasoningStatus.MERGED
        assert loaded.sibling_source_id == str(REASONS_ID)
        assert loaded.requests_made == 3
        assert harness.fake.detail_ids == [SENTENCE_ID, REASONS_ID]
        assert harness.fake.search_params == [{"pageSize": "10", "caseNumber": PAIR_NUMBER}]
        assert loaded.document_id == f"SAOS/{SENTENCE_ID}"
        assert await toc_titles(harness, loaded.document_id) == ["Treść", "WYROK", "UZASADNIENIE"]

    async def test_the_search_sends_nothing_a_recording_does_not_show(self, make_reading: MakeReading) -> None:
        """Only `pageSize` and `caseNumber`, as in the recorded request; a court filter was never recorded."""
        harness = make_reading()

        await harness.service.load(SENTENCE_ID)

        assert set(harness.fake.search_params[0]) == {"pageSize", "caseNumber"}

    async def test_the_reasons_section_holds_the_text_of_the_reasons_record(self, make_reading: MakeReading) -> None:
        harness = make_reading()
        loaded = await harness.service.load(SENTENCE_ID)

        text = await stored_text(harness, loaded.document_id)
        reasons = (await harness.store.get_toc(loaded.document_id))[-1]
        recorded = reading.to_markdown(str(skeleton(REASONS_ID)["data"]["textContent"]))

        assert text.index("## UZASADNIENIE") == reasons.start_pos
        assert reasons.end_pos == len(text)
        # The section is the reasons record from its own heading to its end, nothing cut and nothing added.
        assert reasons.content == recorded[recorded.index("## UZASADNIENIE") :].strip()

    async def test_a_truncated_page_that_still_holds_the_one_reasons_record_is_enough(
        self, make_reading: MakeReading
    ) -> None:
        fake = pair_fake()
        fake.searches[PAIR_NUMBER] = cut_short(pair_page(), total=25, drop_reasons=False)
        harness = make_reading(fake)

        loaded = await harness.service.load(SENTENCE_ID)

        assert loaded.reasoning is ReasoningStatus.MERGED
        assert loaded.requests_made == 3


@pytest.mark.asyncio
class TestASentenceWithNoReasonsRecord:
    async def test_the_source_has_no_reasoning_and_the_text_says_so_as_a_server_note(
        self, make_reading: MakeReading
    ) -> None:
        harness = make_reading()

        loaded = await harness.service.load(LONE_ID)

        assert loaded.reasoning is ReasoningStatus.ABSENT
        assert loaded.requests_made == 2
        assert harness.fake.detail_ids == [LONE_ID]
        assert harness.fake.search_params == [{"pageSize": "10", "caseNumber": LONE_NUMBER}]
        assert loaded.reasoning_note.startswith("Brak uzasadnienia w źródle")
        titles = await toc_titles(harness, loaded.document_id)
        assert titles[-1] == "[Uwaga serwera] Uzasadnienie"
        assert (await stored_text(harness, loaded.document_id)).endswith(
            "Brak uzasadnienia w źródle: SAOS nie podaje uzasadnienia tego orzeczenia. "
            "To uwaga dodana przez serwer, nie tekst orzeczenia."
        )

    async def test_a_reasons_record_of_another_court_is_not_the_reasoning(self, make_reading: MakeReading) -> None:
        """The same case number exists in many courts; only the same court counts."""
        fake = pair_fake()
        fake.searches[PAIR_NUMBER] = with_reasons_of_another_court(pair_page())
        harness = make_reading(fake)

        loaded = await harness.service.load(SENTENCE_ID)

        assert loaded.reasoning is ReasoningStatus.ABSENT
        assert loaded.sibling_source_id is None
        assert harness.fake.detail_ids == [SENTENCE_ID]

    async def test_a_reasons_record_of_another_case_number_is_not_the_reasoning(
        self, make_reading: MakeReading
    ) -> None:
        fake = pair_fake()
        page = pair_page()
        for item in page["items"]:
            if item["judgmentType"] == "REASONS":
                item["courtCases"] = [{"caseNumber": "VIII U 1/25"}]
        fake.searches[PAIR_NUMBER] = page
        harness = make_reading(fake)

        loaded = await harness.service.load(SENTENCE_ID)

        assert loaded.reasoning is ReasoningStatus.ABSENT


@pytest.mark.asyncio
class TestWhenNothingCanBeClaimed:
    async def test_two_reasons_records_of_one_court_are_not_guessed_between(self, make_reading: MakeReading) -> None:
        fake = pair_fake()
        fake.searches[PAIR_NUMBER] = with_second_reasons(pair_page())
        harness = make_reading(fake)

        loaded = await harness.service.load(SENTENCE_ID)

        assert loaded.reasoning is ReasoningStatus.AMBIGUOUS
        assert loaded.candidate_source_ids == [str(REASONS_ID), str(EXTRA_REASONS_ID)]
        assert loaded.sibling_source_id is None
        assert loaded.requests_made == 2
        assert harness.fake.detail_ids == [SENTENCE_ID]
        assert await toc_titles(harness, loaded.document_id) == ["Treść", "WYROK"]

    async def test_a_page_cut_short_with_no_reasons_record_on_it_does_not_prove_absence(
        self, make_reading: MakeReading
    ) -> None:
        fake = pair_fake()
        fake.searches[PAIR_NUMBER] = cut_short(pair_page(), total=25, drop_reasons=True)
        harness = make_reading(fake)

        loaded = await harness.service.load(SENTENCE_ID)

        assert loaded.reasoning is ReasoningStatus.NOT_ASSESSED
        assert loaded.reasoning_note == service.NOTE_TRUNCATED
        assert "Brak uzasadnienia w źródle" not in (await stored_text(harness, loaded.document_id))
        assert loaded.requests_made == 2

    async def test_a_failing_search_leaves_the_sentence_loaded_alone(self, make_reading: MakeReading) -> None:
        harness = make_reading(pair_fake(failing_search=True))

        loaded = await harness.service.load(SENTENCE_ID)

        assert loaded.reasoning is ReasoningStatus.NOT_ASSESSED
        assert loaded.reasoning_note == service.NOTE_SEARCH_FAILED
        assert loaded.requests_made == 2
        assert await toc_titles(harness, loaded.document_id) == ["Treść", "WYROK"]

    @pytest.mark.parametrize("failure", ["fails", "missing"])
    async def test_a_reasons_record_that_cannot_be_fetched_leaves_the_sentence_loaded_alone(
        self, make_reading: MakeReading, failure: str
    ) -> None:
        fake = pair_fake(
            **({"failing_details": [REASONS_ID]} if failure == "fails" else {"missing_details": [REASONS_ID]})
        )
        harness = make_reading(fake)

        loaded = await harness.service.load(SENTENCE_ID)

        assert loaded.reasoning is ReasoningStatus.NOT_ASSESSED
        assert loaded.reasoning_note == service.NOTE_SIBLING_FAILED
        assert loaded.requests_made == 3
        assert loaded.sibling_source_id is None
        assert await toc_titles(harness, loaded.document_id) == ["Treść", "WYROK"]

    async def test_a_record_that_is_not_a_reasons_record_is_not_joined(self, make_reading: MakeReading) -> None:
        fake = pair_fake()
        fake.details[REASONS_ID]["data"]["judgmentType"] = "SENTENCE"
        harness = make_reading(fake)

        loaded = await harness.service.load(SENTENCE_ID)

        assert loaded.reasoning is ReasoningStatus.NOT_ASSESSED
        assert await toc_titles(harness, loaded.document_id) == ["Treść", "WYROK"]

    async def test_a_sentence_with_no_signature_is_not_searched_for(self, make_reading: MakeReading) -> None:
        fake = pair_fake()
        fake.details[SENTENCE_ID]["data"]["courtCases"] = []
        harness = make_reading(fake)

        loaded = await harness.service.load(SENTENCE_ID)

        assert loaded.reasoning is ReasoningStatus.NOT_ASSESSED
        assert loaded.reasoning_note == service.NOTE_NO_SIGNATURE
        assert loaded.requests_made == 1
        assert harness.fake.search_params == []

    async def test_a_decision_is_not_looked_up_as_a_sentence(self, make_reading: MakeReading) -> None:
        fake = pair_fake()
        fake.details[SENTENCE_ID]["data"]["judgmentType"] = "DECISION"
        harness = make_reading(fake)

        loaded = await harness.service.load(SENTENCE_ID)

        assert loaded.reasoning is ReasoningStatus.NOT_ASSESSED
        assert loaded.requests_made == 1
        assert harness.fake.search_params == []


@pytest.mark.asyncio
class TestRecordsThatNeedNoSecondCall:
    async def test_a_sentence_with_the_reasoning_inside_is_one_call(self, make_reading: MakeReading) -> None:
        harness = make_reading()

        loaded = await harness.service.load(NESTED_ID)

        assert loaded.reasoning is ReasoningStatus.IN_RECORD
        assert loaded.requests_made == 1
        assert harness.fake.requests == 1
        assert await toc_titles(harness, loaded.document_id) == ["Treść", "WYROK", "UZASADNIENIE"]

    async def test_a_reasons_record_is_loaded_alone(self, make_reading: MakeReading) -> None:
        harness = make_reading()

        loaded = await harness.service.load(REASONS_ID)

        assert loaded.reasoning is ReasoningStatus.REASONING_RECORD
        assert loaded.requests_made == 1
        assert harness.fake.requests == 1
        assert await toc_titles(harness, loaded.document_id) == ["Treść", "UZASADNIENIE"]

    async def test_a_supreme_court_text_is_loaded_alone_and_no_claim_is_made(self, make_reading: MakeReading) -> None:
        harness = make_reading()

        loaded = await harness.service.load(SUPREME_ID)

        assert loaded.reasoning is ReasoningStatus.NOT_ASSESSED
        assert loaded.requests_made == 1
        assert harness.fake.requests == 1
        assert await toc_titles(harness, loaded.document_id) == ["Treść"]


class TestTheCeilingOnCalls:
    def test_the_ceiling_is_three(self) -> None:
        assert service.MAX_READ_REQUESTS == 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize("source_id", [SENTENCE_ID, REASONS_ID, NESTED_ID, SUPREME_ID, LONE_ID])
    async def test_no_load_sends_more_calls_than_the_ceiling(self, make_reading: MakeReading, source_id: int) -> None:
        harness = make_reading()

        loaded = await harness.service.load(source_id)

        assert loaded.requests_made <= service.MAX_READ_REQUESTS
        assert harness.fake.requests <= service.MAX_READ_REQUESTS
        assert harness.fake.requests == loaded.requests_made

    @pytest.mark.asyncio
    async def test_a_reasons_record_listed_twice_is_one_candidate(self, make_reading: MakeReading) -> None:
        """SAOS may list a record twice when one is inserted between two pages; it is still one record."""
        fake = pair_fake()
        page = pair_page()
        page["items"].append(next(item for item in page["items"] if item["judgmentType"] == "REASONS"))
        fake.searches[PAIR_NUMBER] = page
        harness = make_reading(fake)

        loaded = await harness.service.load(SENTENCE_ID)

        assert loaded.reasoning is ReasoningStatus.MERGED
        assert loaded.candidate_source_ids == []
        assert harness.fake.detail_ids == [SENTENCE_ID, REASONS_ID]
