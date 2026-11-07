"""A loaded document knows its kind, and the content tools say so without changing what they said for acts."""

from __future__ import annotations

import pytest

from law_scrapper_mcp.client.exceptions import DocumentNotLoadedError
from law_scrapper_mcp.models.tool_inputs import is_eli_shaped
from law_scrapper_mcp.models.tool_outputs import DocumentKind
from law_scrapper_mcp.services.content_processor import ContentProcessor, Section
from law_scrapper_mcp.services.content_service import ContentService
from law_scrapper_mcp.services.document_store import DocumentStore, LoadedDocument
from law_scrapper_mcp.services.response_enrichment import content_hints

ACT_ID = "DU/2024/1"
JUDGMENT_ID = "SAOS/554697"
# Derived inside the test from the shape of a recorded judgment (two court headings), with no real words.
JUDGMENT_TEXT = "## WYROK\n\nxxx 0000 xxxx\n\n## UZASADNIENIE\n\nxxx xxxx xxxxxxx xxxx"
ACT_TEXT = "Art. 1\nPrzepis o terminie.\n\nArt. 2\nPrzepis o podatku."


def _sections(text: str) -> list[Section]:
    return ContentProcessor().index_sections(text)


async def _store_with_both() -> DocumentStore:
    store = DocumentStore()
    await store.load(ACT_ID, ACT_TEXT, _sections(ACT_TEXT))
    await store.load(JUDGMENT_ID, JUDGMENT_TEXT, _sections(JUDGMENT_TEXT), kind=DocumentKind.JUDGMENT)
    return store


@pytest.mark.asyncio
class TestKindInTheStore:
    async def test_a_document_loaded_the_old_way_is_an_act(self) -> None:
        store = DocumentStore()
        await store.load(ACT_ID, ACT_TEXT, _sections(ACT_TEXT))

        assert await store.document_kind(ACT_ID) is DocumentKind.ACT

    async def test_a_document_built_without_a_kind_is_an_act(self) -> None:
        """The dataclass default is read by callers that build the document themselves, not through `load`."""
        document = LoadedDocument(eli=ACT_ID, markdown=ACT_TEXT, sections=_sections(ACT_TEXT))

        assert document.kind is DocumentKind.ACT

    async def test_a_judgment_keeps_its_kind(self) -> None:
        store = await _store_with_both()

        assert await store.document_kind(JUDGMENT_ID) is DocumentKind.JUDGMENT
        assert await store.document_kind(ACT_ID) is DocumentKind.ACT

    async def test_the_kind_of_a_document_that_is_not_loaded_raises(self) -> None:
        store = DocumentStore()

        with pytest.raises(DocumentNotLoadedError):
            await store.document_kind(JUDGMENT_ID)

    async def test_reading_the_kind_does_not_extend_the_ttl(self) -> None:
        store = await _store_with_both()
        before = store._store[JUDGMENT_ID].last_accessed  # noqa: SLF001 - the point of the test

        await store.document_kind(JUDGMENT_ID)

        assert store._store[JUDGMENT_ID].last_accessed == before  # noqa: SLF001

    async def test_the_listing_names_id_and_kind_next_to_eli(self) -> None:
        store = await _store_with_both()

        page = await store.list_documents_page()

        by_eli = {info.eli: info for info in page.documents}
        assert set(by_eli) == {ACT_ID, JUDGMENT_ID}
        assert (by_eli[ACT_ID].document_id, by_eli[ACT_ID].document_kind) == (ACT_ID, DocumentKind.ACT)
        assert (by_eli[JUDGMENT_ID].document_id, by_eli[JUDGMENT_ID].document_kind) == (
            JUDGMENT_ID,
            DocumentKind.JUDGMENT,
        )

    async def test_overlapping_sections_are_refused_in_words_that_fit_any_document(self) -> None:
        store = DocumentStore()
        overlapping = [
            Section(id="a", title="A", level=1, start_pos=0, end_pos=10),
            Section(id="b", title="B", level=1, start_pos=5, end_pos=20),
        ]

        with pytest.raises(ValueError, match="Sekcje dokumentu SAOS/1 nakładają się"):
            await store.load("SAOS/1", "x" * 30, overlapping, kind=DocumentKind.JUDGMENT)


@pytest.mark.asyncio
class TestContentToolsOnAJudgment:
    async def test_the_table_of_contents_lists_the_court_headings(self) -> None:
        service = ContentService(await _store_with_both())

        output = await service.read(JUDGMENT_ID)

        assert [entry["id"] for entry in output.toc] == ["WYROK", "UZASADNIENIE"]
        assert output.eli == output.document_id == JUDGMENT_ID
        assert output.document_kind is DocumentKind.JUDGMENT

    async def test_a_section_is_read_with_paging(self) -> None:
        service = ContentService(await _store_with_both())

        output = await service.read(JUDGMENT_ID, section="UZASADNIENIE", limit=15, offset=0)

        assert output.content == "## UZASADNIENIE"
        assert output.page_info.was_truncated is True
        assert output.page_info.next_offset == 15
        assert output.document_kind is DocumentKind.JUDGMENT

    async def test_a_search_finds_the_text_and_names_the_section(self) -> None:
        service = ContentService(await _store_with_both())

        output = await service.search(JUDGMENT_ID, "xxxxxxx")

        assert output.total_matches == 1
        assert output.matches[0]["section_id"] == "UZASADNIENIE"
        assert output.eli == output.document_id == JUDGMENT_ID
        assert output.document_kind is DocumentKind.JUDGMENT

    async def test_an_act_in_the_same_store_is_described_as_an_act(self) -> None:
        service = ContentService(await _store_with_both())

        toc = await service.read(ACT_ID)
        section = await service.read(ACT_ID, section="Art. 1")
        search = await service.search(ACT_ID, "podatku")

        for output in (toc, section, search):
            assert output.eli == output.document_id == ACT_ID
            assert output.document_kind is DocumentKind.ACT


class TestTextsAreTheOnesActsAlwaysHad:
    """Golden texts: an ELI keeps every character of the message it had before judgments existed."""

    def test_not_loaded_message_for_an_act_is_byte_identical(self) -> None:
        assert str(DocumentNotLoadedError(ACT_ID)) == (
            "Dokument DU/2024/1 nie jest załadowany. Użyj get_act_details(eli='DU/2024/1', load_content=true)"
        )

    def test_not_loaded_message_for_a_url_form_eli_is_byte_identical(self) -> None:
        eli = "https://api.sejm.gov.pl/eli/DU/2024/1"

        assert str(DocumentNotLoadedError(eli)) == (
            f"Dokument {eli} nie jest załadowany. Użyj get_act_details(eli='{eli}', load_content=true)"
        )

    def test_not_loaded_message_for_a_judgment_names_the_source_tool(self) -> None:
        message = str(DocumentNotLoadedError(JUDGMENT_ID))

        assert message == (
            "Dokument SAOS/554697 nie jest załadowany. Wczytaj go narzędziem źródła "
            "(orzeczenie z SAOS: get_saos_judgment_details z load_content=true)."
        )
        assert "get_act_details" not in message

    @pytest.mark.asyncio
    async def test_missing_section_message_for_an_act_is_byte_identical(self) -> None:
        service = ContentService(await _store_with_both())

        with pytest.raises(ValueError) as exc_info:
            await service.read(ACT_ID, section="Art. 99")

        assert str(exc_info.value) == (
            "Sekcja 'Art. 99' nie znaleziona w akcie DU/2024/1. "
            "Użyj read_act_content(eli='DU/2024/1') aby zobaczyć dostępne sekcje."
        )

    @pytest.mark.asyncio
    async def test_missing_section_message_for_a_judgment_does_not_say_act(self) -> None:
        service = ContentService(await _store_with_both())

        with pytest.raises(ValueError) as exc_info:
            await service.read(JUDGMENT_ID, section="Zażalenie")

        assert str(exc_info.value) == (
            "Sekcja 'Zażalenie' nie znaleziona w dokumencie SAOS/554697. "
            "Użyj read_act_content(eli='SAOS/554697') aby zobaczyć dostępne sekcje."
        )

    def test_the_content_hint_for_an_act_is_byte_identical(self) -> None:
        (hint,) = content_hints(ACT_ID, True)

        assert hint.message == "Wyszukaj konkretne terminy w treści tego aktu."
        assert hint.tool == "search_in_act"
        assert hint.parameters == {"eli": ACT_ID}

    def test_the_content_hint_for_a_judgment_says_document(self) -> None:
        (hint,) = content_hints(JUDGMENT_ID, True)

        assert hint.message == "Wyszukaj konkretne terminy w treści tego dokumentu."
        assert hint.parameters == {"eli": JUDGMENT_ID}

    def test_no_hint_without_sections(self) -> None:
        assert content_hints(JUDGMENT_ID, False) == []


class TestEliShape:
    @pytest.mark.parametrize(
        "value",
        ["DU/2024/1", "MP/2023/500", "POL_WOJ_PM/2024/12", "https://api.sejm.gov.pl/eli/DU/2024/1", "DU/2024/1/"],
    )
    def test_an_eli_is_recognised(self, value: str) -> None:
        assert is_eli_shaped(value) is True

    @pytest.mark.parametrize(
        "value",
        ["SAOS/554697", "SAOS/554697/x", "DU/abc/1", "", "554697", "https://example.test/DU/2024/1"],
    )
    def test_another_identifier_is_not(self, value: str) -> None:
        assert is_eli_shaped(value) is False
