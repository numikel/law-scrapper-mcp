"""Loading a judgment: the identifier, the kind in the store, reading it back, the errors, and what must not leak.

The details are the skeleton recordings (see `saos_reading_testing`); a test that needs words or people puts
placeholders into a copy of one, because a skeleton has neither.
"""

from __future__ import annotations

import json

import pytest
from saos_reading_testing import (
    REASONS_ID,
    SENTENCE_ID,
    MakeReading,
    pair_fake,
    skeleton,
)

from law_scrapper_mcp.client.exceptions import ApiUnavailableError, SejmApiError
from law_scrapper_mcp.models.tool_outputs import DocumentKind
from law_scrapper_mcp.services.content_service import ContentService
from law_scrapper_mcp_saos.models import ReasoningStatus, SaosDetail, SaosDetailData, SaosLoadedJudgment, SaosTextData

JUDGE = "Zofia Wzorcowa-Testowa"
REPORTER = "Jan Przykładowy-Testowy"
# A reasons text in the recorded shape, with placeholder words and the anonymisation markers SAOS leaves.
DERIVED_TEXT = (
    '<div><h2>UZASADNIENIE</h2><p>Sąd <span class="anon-block">(...)</span> oddalił powództwo '
    '<span class="anon-block">D. P. (1)</span> na podstawie art. 207 k.p.</p></div>'
)


@pytest.mark.asyncio
class TestWhatIsStored:
    async def test_the_judgment_is_stored_under_its_document_id_as_a_judgment(self, make_reading: MakeReading) -> None:
        harness = make_reading()

        loaded = await harness.service.load(REASONS_ID)

        assert loaded.document_id == f"SAOS/{REASONS_ID}"
        assert await harness.store.is_loaded(loaded.document_id)
        assert await harness.store.document_kind(loaded.document_id) is DocumentKind.JUDGMENT

    async def test_the_numbers_in_the_answer_are_the_ones_of_the_stored_document(
        self, make_reading: MakeReading
    ) -> None:
        harness = make_reading()

        loaded = await harness.service.load(SENTENCE_ID)

        (info,) = await harness.store.list_documents()
        assert loaded.size_bytes == info["size_bytes"]
        assert loaded.section_count == info["section_count"] == 3
        assert info["document_kind"] is DocumentKind.JUDGMENT

    async def test_a_number_with_spaces_or_an_integer_is_accepted(self, make_reading: MakeReading) -> None:
        harness = make_reading()

        first = await harness.service.load(f" {REASONS_ID} ")
        second = await harness.service.load(REASONS_ID)

        assert first.document_id == second.document_id

    async def test_a_second_load_asks_again_and_replaces_the_document(self, make_reading: MakeReading) -> None:
        """The detail is never cached (see `SaosClient.judgment`); the loaded document is the reuse."""
        harness = make_reading()

        await harness.service.load(REASONS_ID)
        await harness.service.load(REASONS_ID)

        assert harness.fake.detail_ids == [REASONS_ID, REASONS_ID]
        assert len(await harness.store.list_documents()) == 1


@pytest.mark.asyncio
class TestReadingItBack:
    """The loaded document is read with the core's content service, as `read_act_content` does."""

    async def test_the_table_of_contents_and_a_section_come_with_the_judgment_kind(
        self, make_reading: MakeReading
    ) -> None:
        harness = make_reading()
        loaded = await harness.service.load(SENTENCE_ID)
        content = ContentService(harness.store)

        toc = await content.read(loaded.document_id)
        page = await content.read(loaded.document_id, section="UZASADNIENIE", limit=40, offset=0)

        assert [entry["title"] for entry in toc.toc] == ["Treść", "WYROK", "UZASADNIENIE"]
        assert toc.document_kind is DocumentKind.JUDGMENT
        assert toc.eli == toc.document_id == loaded.document_id
        assert page.content.startswith("## UZASADNIENIE")
        assert page.page_info.was_truncated is True
        assert page.page_info.next_offset == 40

    async def test_a_search_names_the_section_of_each_hit(self, make_reading: MakeReading) -> None:
        harness = make_reading()
        loaded = await harness.service.load(SENTENCE_ID)

        result = await ContentService(harness.store).search(loaded.document_id, "UZASADNIENIE")

        assert result.total_matches == 1
        assert result.matches[0]["section_id"] == "UZASADNIENIE"
        assert result.document_kind is DocumentKind.JUDGMENT


@pytest.mark.asyncio
class TestTheMarkersOfAnonymisation:
    async def test_the_markers_stay_in_the_text_and_their_tag_does_not(self, make_reading: MakeReading) -> None:
        fake = pair_fake()
        fake.details[REASONS_ID]["data"]["textContent"] = DERIVED_TEXT
        harness = make_reading(fake)

        loaded = await harness.service.load(REASONS_ID)

        text = str(harness.store._store[loaded.document_id].markdown)  # noqa: SLF001
        assert "(...)" in text
        assert "D. P. (1)" in text
        assert "anon-block" not in text


@pytest.mark.asyncio
class TestWhatMustNotLeak:
    async def test_judges_and_reporters_reach_neither_the_answer_nor_the_stored_text(
        self, make_reading: MakeReading
    ) -> None:
        fake = pair_fake()
        for source_id in (SENTENCE_ID, REASONS_ID):
            data = fake.details[source_id]["data"]
            data["judges"] = [{"name": JUDGE, "function": None, "specialRoles": ["PRESIDING_JUDGE"]}]
            data["courtReporters"] = [REPORTER]
        # Positive control: SAOS is made to send both, so a leak would be seen.
        served = json.dumps(fake.details[SENTENCE_ID], ensure_ascii=False)
        assert JUDGE in served and REPORTER in served
        harness = make_reading(fake)

        loaded = await harness.service.load(SENTENCE_ID)

        blob = loaded.model_dump_json() + str(harness.store._store[loaded.document_id].markdown)  # noqa: SLF001
        assert JUDGE not in blob
        assert REPORTER not in blob


class TestTheModelsThatReadADetail:
    def test_no_model_names_the_judges_or_the_reporters(self) -> None:
        for model in (SaosDetail, SaosDetailData, SaosTextData):
            fields = set(model.model_fields)
            assert not {"judges", "court_reporters", "courtReporters"} & fields, model.__name__

    def test_only_the_text_model_reads_the_text(self) -> None:
        assert "text_content" in SaosTextData.model_fields
        assert "text_content" not in SaosDetailData.model_fields
        assert "text_content" not in SaosDetail.model_fields

    def test_the_metadata_model_still_reads_a_detail_that_has_a_text(self) -> None:
        detail = SaosDetail.model_validate(skeleton(SENTENCE_ID))

        assert detail.data.id == SENTENCE_ID
        assert not hasattr(detail.data, "text_content")


@pytest.mark.asyncio
class TestRefusals:
    async def test_a_source_id_that_is_not_a_number_is_refused_before_any_call(self, make_reading: MakeReading) -> None:
        harness = make_reading()

        with pytest.raises(ValueError, match="source_id"):
            await harness.service.load("abc")

        assert harness.fake.requests == 0

    async def test_an_unknown_judgment_is_a_polish_error_and_stores_nothing(self, make_reading: MakeReading) -> None:
        harness = make_reading()

        with pytest.raises(ValueError, match="Nie znaleziono orzeczenia o identyfikatorze 999 w SAOS"):
            await harness.service.load(999)

        assert await harness.store.list_documents() == []

    async def test_a_judgment_with_no_text_is_refused(self, make_reading: MakeReading) -> None:
        fake = pair_fake()
        fake.details[REASONS_ID]["data"]["textContent"] = "  \n "
        harness = make_reading(fake)

        with pytest.raises(ValueError, match="nie ma w SAOS tekstu"):
            await harness.service.load(REASONS_ID)

        assert await harness.store.list_documents() == []

    async def test_an_answer_that_is_not_a_detail_is_refused(self, make_reading: MakeReading) -> None:
        fake = pair_fake()
        fake.details[REASONS_ID] = {"unexpected": True}
        harness = make_reading(fake)

        with pytest.raises(SejmApiError, match="nie jest szczegółami orzeczenia"):
            await harness.service.load(REASONS_ID)

        assert await harness.store.list_documents() == []

    async def test_a_failing_first_call_fails_the_load(self, make_reading: MakeReading) -> None:
        harness = make_reading(pair_fake(failing_details=[REASONS_ID]))

        with pytest.raises((SejmApiError, ApiUnavailableError)):
            await harness.service.load(REASONS_ID)

        assert await harness.store.list_documents() == []


class TestTheAnswerModel:
    def test_the_required_fields(self) -> None:
        required = set(SaosLoadedJudgment.model_json_schema()["required"])

        assert required == {
            "document_id",
            "size_bytes",
            "section_count",
            "reasoning",
            "reasoning_note",
            "requests_made",
        }

    def test_the_statuses_are_the_documented_six(self) -> None:
        assert {status.value for status in ReasoningStatus} == {
            "in_record",
            "merged",
            "absent",
            "ambiguous",
            "reasoning_record",
            "not_assessed",
        }
