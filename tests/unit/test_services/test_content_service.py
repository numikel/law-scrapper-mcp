"""Tests for the act content and in-act search domain service."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from law_scrapper_mcp.models.pagination import MAX_CONTEXT_CHARS, MAX_SECTION_CHAR_LIMIT
from law_scrapper_mcp.services.content_service import ContentService
from law_scrapper_mcp.services.document_store import SearchHit

pytestmark = pytest.mark.asyncio


def _store(**overrides: object) -> AsyncMock:
    store = AsyncMock()
    store.get_toc.return_value = []
    store.get_section.return_value = None
    store.search.return_value = []
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
        hits = [
            SearchHit(section_id=f"art_{n}", section_title=f"Art. {n}", context="ctx", match_start=n, match_end=n + 3)
            for n in range(1, 4)
        ]
        service = ContentService(_store(search=hits))

        output = await service.search("DU/2024/1", "podatek", limit=1, offset=1)

        assert output.matches == [
            {"section_id": "art_2", "section_title": "Art. 2", "context": "ctx", "position": "2-5"}
        ]
        assert output.total_matches == 3
        assert output.page_info.returned_count == 1
        assert output.page_info.next_offset == 2

    async def test_context_chars_is_clamped_before_reaching_the_store(self) -> None:
        store = _store(search=[])
        service = ContentService(store)

        await service.search("DU/2024/1", "podatek", context_chars=10**6)

        store.search.assert_awaited_once_with("DU/2024/1", "podatek", MAX_CONTEXT_CHARS)

    async def test_non_integer_limit_is_reported_in_polish(self) -> None:
        service = ContentService(_store(search=[]))

        with pytest.raises(ValueError, match="Parametr 'limit' musi być liczbą całkowitą."):
            await service.search("DU/2024/1", "podatek", limit="abc")

    async def test_negative_offset_is_rejected(self) -> None:
        service = ContentService(_store(search=[]))

        with pytest.raises(ValueError, match="Parametr 'offset' nie może być ujemny."):
            await service.search("DU/2024/1", "podatek", offset=-1)
