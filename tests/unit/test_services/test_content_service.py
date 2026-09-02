"""Tests for the act content and in-act search domain service."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from law_scrapper_mcp.models.pagination import MAX_CONTEXT_CHARS, MAX_SECTION_CHAR_LIMIT
from law_scrapper_mcp.services.content_processor import ContentProcessor, Section
from law_scrapper_mcp.services.content_service import ContentService
from law_scrapper_mcp.services.document_store import DocumentStore, SearchHit

pytestmark = pytest.mark.asyncio


def _store(**overrides: object) -> AsyncMock:
    store = AsyncMock()
    store.get_toc.return_value = []
    store.get_section.return_value = None
    store.scan.return_value = []
    store.hydrate.return_value = []
    for name, value in overrides.items():
        getattr(store, name).return_value = value
    return store


def _sections(count: int) -> list[SimpleNamespace]:
    return [SimpleNamespace(id=f"art_{n}", title=f"Art. {n}", level=1) for n in range(1, count + 1)]


class TestReadTableOfContents:
    async def test_paginates_entries_and_keeps_the_full_total(self) -> None:
        service = ContentService(_store(get_toc=_sections(5)))

        output = await service.read("DU/2024/1", limit=2, offset=1)

        assert [entry["id"] for entry in output.toc] == ["art_2", "art_3"]
        assert output.section_id is None
        assert output.page_info.total_count == 5
        assert output.page_info.returned_count == 2
        assert output.page_info.was_truncated is True
        assert output.page_info.next_offset == 3
        assert output.page_info.unit == "items"

    async def test_empty_act_reports_zero_without_truncation(self) -> None:
        service = ContentService(_store(get_toc=[]))

        output = await service.read("DU/2024/1")

        assert output.toc == []
        assert output.page_info.total_count == 0
        assert output.page_info.was_truncated is False
        assert output.page_info.next_offset is None


class TestReadSection:
    async def test_paginates_by_characters(self) -> None:
        service = ContentService(_store(get_section="abcdefghij"))

        output = await service.read("DU/2024/1", section="Art. 1", limit=4, offset=4)

        assert output.content == "efgh"
        assert output.section_id == "Art. 1"
        assert output.page_info.unit == "characters"
        assert output.page_info.total_count == 10
        assert output.page_info.next_offset == 8

    async def test_offset_past_the_end_returns_an_empty_page(self) -> None:
        service = ContentService(_store(get_section="abc"))

        output = await service.read("DU/2024/1", section="Art. 1", limit=10, offset=99)

        assert output.content == ""
        assert output.page_info.total_count == 3
        assert output.page_info.was_truncated is False
        assert output.page_info.next_offset is None

    async def test_section_limit_is_clamped_to_the_maximum(self) -> None:
        service = ContentService(_store(get_section="abc"))

        output = await service.read("DU/2024/1", section="Art. 1", limit=10**9)

        assert output.page_info.limit == MAX_SECTION_CHAR_LIMIT

    async def test_missing_section_raises_a_polish_error_naming_the_act(self) -> None:
        service = ContentService(_store(get_section=None))

        with pytest.raises(ValueError) as exc_info:
            await service.read("DU/2024/1", section="Art. 99")

        message = str(exc_info.value)
        assert "Sekcja 'Art. 99' nie znaleziona w akcie DU/2024/1" in message
        assert "read_act_content(eli='DU/2024/1')" in message


class TestSearch:
    async def test_maps_hits_and_paginates(self) -> None:
        spans = [(n, n + 3) for n in range(1, 4)]
        page_hits = [SearchHit(section_id="art_2", section_title="Art. 2", context="ctx", match_start=2, match_end=5)]
        service = ContentService(_store(scan=spans, hydrate=page_hits))

        output = await service.search("DU/2024/1", "podatek", limit=1, offset=1)

        assert output.matches == [
            {"section_id": "art_2", "section_title": "Art. 2", "context": "ctx", "position": "2-5"}
        ]
        assert output.total_matches == 3
        assert output.page_info.returned_count == 1
        assert output.page_info.next_offset == 2

    async def test_context_chars_is_clamped_before_reaching_the_store(self) -> None:
        store = _store(scan=[(0, 3)], hydrate=[])
        service = ContentService(store)

        await service.search("DU/2024/1", "podatek", context_chars=10**6)

        store.hydrate.assert_awaited_once_with("DU/2024/1", [(0, 3)], context_chars=MAX_CONTEXT_CHARS)

    async def test_hydration_is_limited_to_the_requested_page(self) -> None:
        spans = [(n, n + 3) for n in range(1_000)]
        store = _store(scan=spans, hydrate=[])
        service = ContentService(store)

        await service.search("DU/2024/1", "podatek", limit=20, offset=0)

        store.hydrate.assert_awaited_once()
        hydrated_spans = store.hydrate.await_args.args[1]
        assert len(hydrated_spans) == 20
        assert hydrated_spans == spans[:20]

    async def test_total_count_is_exact_regardless_of_the_page(self) -> None:
        spans = [(n, n + 3) for n in range(1_000)]
        service = ContentService(_store(scan=spans, hydrate=[]))

        for limit, offset in ((1, 0), (20, 0), (5, 900), (100, 999)):
            output = await service.search("DU/2024/1", "podatek", limit=limit, offset=offset)
            assert output.page_info.total_count == 1_000
            assert output.total_matches == 1_000

    async def test_non_integer_limit_is_reported_in_polish(self) -> None:
        store = _store()
        service = ContentService(store)

        with pytest.raises(ValueError, match="Parametr 'limit' musi być liczbą całkowitą."):
            await service.search("DU/2024/1", "podatek", limit="abc")

        store.scan.assert_not_awaited()

    async def test_negative_offset_is_rejected(self) -> None:
        store = _store()
        service = ContentService(store)

        with pytest.raises(ValueError, match="Parametr 'offset' nie może być ujemny."):
            await service.search("DU/2024/1", "podatek", offset=-1)

        store.scan.assert_not_awaited()

    async def test_oversized_context_chars_is_clamped_not_rejected(self) -> None:
        service = ContentService(_store(scan=[(0, 3)], hydrate=[]))

        output = await service.search("DU/2024/1", "podatek", context_chars=5000)

        assert output.context_chars_requested == 5000
        assert output.context_chars_applied == MAX_CONTEXT_CHARS

    async def test_context_chars_within_the_limit_is_reported_unchanged(self) -> None:
        service = ContentService(_store(scan=[(0, 3)], hydrate=[]))

        output = await service.search("DU/2024/1", "podatek", context_chars=300)

        assert output.context_chars_requested == 300
        assert output.context_chars_applied == 300


class TestSearchMatchesTheNaiveImplementation:
    """K4: the change is performance-only; results must be bit-identical."""

    @staticmethod
    def _naive(markdown: str, sections: list[Section], query: str, context_chars: int) -> list[dict[str, str]]:
        import re

        matches: list[dict[str, str]] = []
        for match in re.compile(re.escape(query), re.IGNORECASE).finditer(markdown):
            start = max(0, match.start() - context_chars)
            end = min(len(markdown), match.end() + context_chars)
            section_id, section_title = "unknown", "Unknown section"
            for section in sections:
                if section.start_pos <= match.start() < (section.end_pos or len(markdown)):
                    section_id, section_title = section.id, section.title
                    break
            matches.append(
                {
                    "section_id": section_id,
                    "section_title": section_title,
                    "context": markdown[start:end],
                    "position": f"{match.start()}-{match.end()}",
                }
            )
        return matches

    async def test_results_match_the_naive_reference_implementation(self) -> None:
        processor = ContentProcessor()
        markdown = "\n\n".join(
            f"Art. {n}\nPrzepis o terminie i o podatku numer {n}. Termin wynosi {n} dni." for n in range(1, 61)
        )
        sections = processor.index_sections(markdown)
        store = DocumentStore()
        await store.load("DU/2024/1", markdown, sections)
        service = ContentService(store)

        # The empty query is in the matrix on purpose: `finditer("")` yields a
        # zero-width span per position, and a span filter written as
        # `start < end` drops every one of them while leaving total_count
        # intact — a K4 regression that no other query in this list exposes.
        for query in ("podatku", "termin", "Art. 5", "niczego-tu-nie-ma", ""):
            for limit, offset in ((20, 0), (5, 3), (1, 0), (100, 0), (10, 500)):
                expected = self._naive(markdown, sections, query, 500)
                output = await service.search("DU/2024/1", query, context_chars=500, limit=limit, offset=offset)

                assert output.matches == expected[offset : offset + limit], (query, limit, offset)
                assert output.total_matches == len(expected), (query, limit, offset)
                # Stated as its own contract rather than left as an implication
                # of the line above: page metadata must describe the payload
                # that actually shipped.
                assert len(output.matches) == output.page_info.returned_count, (query, limit, offset)


class TestSearchIsAtomicAgainstAConcurrentReload:
    """The scan/hydrate split is only safe while no critical section awaits.

    This pins the invariant itself, not one of its effects. It passes today by
    construction and starts failing on the day someone adds a suspension point
    inside `DocumentStore` — which is exactly when the page metadata could
    begin describing a document that no longer exists.
    """

    async def test_reload_during_search_cannot_desynchronise_the_page(self) -> None:
        import asyncio

        processor = ContentProcessor()
        markdown = "\n\n".join(f"Art. {n}\nPrzepis o podatku numer {n}." for n in range(1, 41))
        store = DocumentStore()
        await store.load("DU/2024/1", markdown, processor.index_sections(markdown))
        service = ContentService(store)

        shorter = "Art. 1\nPrzepis o podatku."
        reload_task = asyncio.create_task(store.load("DU/2024/1", shorter, processor.index_sections(shorter)))
        output = await service.search("DU/2024/1", "podatku", limit=20, offset=0)
        await reload_task

        assert len(output.matches) == output.page_info.returned_count
        assert output.total_matches == 40
        assert output.page_info.returned_count == 20
