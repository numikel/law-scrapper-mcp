"""Tests for DocumentStore service."""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import overload

import pytest

from law_scrapper_mcp.client.exceptions import ContentTooLargeError, DocumentNotLoadedError
from law_scrapper_mcp.services.content_processor import Section
from law_scrapper_mcp.services.document_store import (
    UNKNOWN_SECTION,
    DocumentStore,
    LoadedDocument,
    SearchHit,
    section_for_position,
)


async def _search(store: DocumentStore, eli: str, query: str, *, context_chars: int = 500) -> list[SearchHit]:
    """Compose `scan` and `hydrate` the way `ContentService.search` does, unpaginated."""
    spans = await store.scan(eli, query)
    return await store.hydrate(eli, spans, context_chars=context_chars)


@pytest.mark.asyncio
class TestDocumentStoreBasicOperations:
    """Tests for basic document store operations."""

    async def test_load_document(self, document_store: DocumentStore):
        """Test loading a document into the store."""
        sections = [
            Section(id="art_1", title="Art. 1.", level=2, start_pos=0, end_pos=50),
        ]
        await document_store.load("DU/2024/1", "Test content", sections)

        assert await document_store.is_loaded("DU/2024/1")

    async def test_is_loaded_false_for_missing(self, document_store: DocumentStore):
        """Test is_loaded returns False for non-existent document."""
        assert not await document_store.is_loaded("DU/2024/999")

    async def test_get_toc(self, document_store: DocumentStore):
        """Test getting table of contents."""
        sections = [
            Section(id="art_1", title="Art. 1.", level=2, start_pos=0, end_pos=50),
            Section(id="art_2", title="Art. 2.", level=2, start_pos=50, end_pos=100),
        ]
        await document_store.load("DU/2024/1", "Test content", sections)

        toc = await document_store.get_toc("DU/2024/1")
        assert len(toc) == 2
        assert toc[0].id == "art_1"
        assert toc[1].id == "art_2"

    async def test_evict_document(self, document_store: DocumentStore):
        """Test manually evicting a document."""
        sections = [Section(id="art_1", title="Art. 1.", level=2, start_pos=0)]
        await document_store.load("DU/2024/1", "Test content", sections)

        assert await document_store.is_loaded("DU/2024/1")

        await document_store.evict("DU/2024/1")
        assert not await document_store.is_loaded("DU/2024/1")

    async def test_evict_nonexistent_document(self, document_store: DocumentStore):
        """Test evicting a document that doesn't exist."""
        await document_store.evict("DU/2024/999")  # Should not raise


@pytest.mark.asyncio
class TestGetSection:
    """Tests for getting sections from documents."""

    async def test_get_section_by_id(self, document_store: DocumentStore):
        """Test getting section by exact ID."""
        markdown = "Art. 1. Test content for article 1.\n\nArt. 2. Test content for article 2."
        sections = [
            Section(
                id="art_1",
                title="Art. 1.",
                level=2,
                start_pos=0,
                end_pos=37,
                content="Art. 1. Test content for article 1.",
            ),
        ]
        await document_store.load("DU/2024/1", markdown, sections)

        content = await document_store.get_section("DU/2024/1", "art_1")
        assert content is not None
        assert "article 1" in content

    async def test_get_section_case_insensitive(self, document_store: DocumentStore):
        """Test getting section with case-insensitive matching."""
        sections = [
            Section(
                id="art_1",
                title="Art. 1.",
                level=2,
                start_pos=0,
                content="Test content",
            ),
        ]
        await document_store.load("DU/2024/1", "Test content", sections)

        content = await document_store.get_section("DU/2024/1", "ART_1")
        assert content is not None

    async def test_get_section_by_title_prefix(self, document_store: DocumentStore):
        """Test getting section by title prefix."""
        sections = [
            Section(
                id="art_1",
                title="Art. 1. Long title here",
                level=2,
                start_pos=0,
                content="Test content",
            ),
        ]
        await document_store.load("DU/2024/1", "Test content", sections)

        content = await document_store.get_section("DU/2024/1", "art. 1")
        assert content is not None

    async def test_get_section_art_pattern_matching(self, document_store: DocumentStore):
        """Test getting section using Art. X pattern."""
        sections = [
            Section(
                id="art_123",
                title="Art. 123. Test article",
                level=2,
                start_pos=0,
                content="Content of article 123",
            ),
        ]
        await document_store.load("DU/2024/1", "Content of article 123", sections)

        content = await document_store.get_section("DU/2024/1", "art. 123")
        assert content is not None
        assert "article 123" in content

    async def test_get_section_not_found(self, document_store: DocumentStore):
        """Test getting non-existent section."""
        sections = [
            Section(id="art_1", title="Art. 1.", level=2, start_pos=0, content="Test"),
        ]
        await document_store.load("DU/2024/1", "Test", sections)

        content = await document_store.get_section("DU/2024/1", "nonexistent")
        assert content is None

    async def test_get_section_document_not_loaded(self, document_store: DocumentStore):
        """Test getting section from non-loaded document raises error."""
        with pytest.raises(DocumentNotLoadedError, match="Dokument DU/2024/999 nie jest załadowany"):
            await document_store.get_section("DU/2024/999", "art_1")


@pytest.mark.asyncio
class TestSearchInDocument:
    """Tests for searching within documents."""

    async def test_search_basic(self, document_store: DocumentStore):
        """Test basic text search within a document."""
        markdown = "Art. 1. This is test content with keyword.\n\nArt. 2. More text here."
        sections = [
            Section(
                id="art_1",
                title="Art. 1.",
                level=2,
                start_pos=0,
                end_pos=43,
                content=markdown[:43],
            ),
        ]
        await document_store.load("DU/2024/1", markdown, sections)

        hits = await _search(document_store, "DU/2024/1", "keyword")
        assert len(hits) == 1
        assert "keyword" in hits[0].context

    async def test_search_case_insensitive(self, document_store: DocumentStore):
        """Test case-insensitive search."""
        markdown = "Art. 1. Testing KEYWORD here."
        sections = [
            Section(id="art_1", title="Art. 1.", level=2, start_pos=0, content=markdown),
        ]
        await document_store.load("DU/2024/1", markdown, sections)

        hits = await _search(document_store, "DU/2024/1", "keyword")
        assert len(hits) == 1
        assert "KEYWORD" in hits[0].context

    async def test_search_multiple_matches(self, document_store: DocumentStore):
        """Test search with multiple matches."""
        markdown = "Art. 1. Test keyword.\n\nArt. 2. Another keyword mention."
        sections = [
            Section(id="art_1", title="Art. 1.", level=2, start_pos=0, end_pos=21),
            Section(id="art_2", title="Art. 2.", level=2, start_pos=23, end_pos=58),
        ]
        await document_store.load("DU/2024/1", markdown, sections)

        hits = await _search(document_store, "DU/2024/1", "keyword")
        assert len(hits) == 2

    async def test_search_with_context(self, document_store: DocumentStore):
        """Test search context extraction."""
        markdown = "This is a long text with the keyword in the middle and more text after."
        sections = [
            Section(id="section_1", title="Section", level=1, start_pos=0),
        ]
        await document_store.load("DU/2024/1", markdown, sections)

        hits = await _search(document_store, "DU/2024/1", "keyword", context_chars=20)
        assert len(hits) == 1
        # Context should include text before and after
        assert "keyword" in hits[0].context
        assert len(hits[0].context) <= len(markdown)

    async def test_search_identifies_section(self, document_store: DocumentStore):
        """Test that search identifies the correct section for matches."""
        markdown = "Art. 1. No match here.\n\nArt. 2. This has keyword."
        sections = [
            Section(
                id="art_1",
                title="Art. 1.",
                level=2,
                start_pos=0,
                end_pos=22,
                content="Art. 1. No match here.",
            ),
            Section(
                id="art_2",
                title="Art. 2.",
                level=2,
                start_pos=24,
                end_pos=49,
                content="Art. 2. This has keyword.",
            ),
        ]
        await document_store.load("DU/2024/1", markdown, sections)

        hits = await _search(document_store, "DU/2024/1", "keyword")
        assert len(hits) == 1
        assert hits[0].section_id == "art_2"
        assert hits[0].section_title == "Art. 2."

    async def test_search_no_matches(self, document_store: DocumentStore):
        """Test search with no matches."""
        markdown = "Art. 1. Some content here."
        sections = [Section(id="art_1", title="Art. 1.", level=2, start_pos=0)]
        await document_store.load("DU/2024/1", markdown, sections)

        hits = await _search(document_store, "DU/2024/1", "nonexistent")
        assert len(hits) == 0

    async def test_search_document_not_loaded(self, document_store: DocumentStore):
        """Test searching in non-loaded document raises error."""
        with pytest.raises(DocumentNotLoadedError, match="Dokument DU/2024/999 nie jest załadowany"):
            await _search(document_store, "DU/2024/999", "keyword")


@pytest.mark.asyncio
class TestTTLExpiration:
    """Tests for TTL-based document expiration."""

    async def test_document_expires_after_ttl(self):
        """Test that documents expire after TTL."""
        store = DocumentStore(max_documents=10, ttl=2)
        sections = [Section(id="art_1", title="Art. 1.", level=2, start_pos=0)]
        await store.load("DU/2024/1", "Test content", sections)

        assert await store.is_loaded("DU/2024/1")

        time.sleep(2.5)

        assert not await store.is_loaded("DU/2024/1")

    async def test_expired_document_raises_error_on_access(self):
        """Test that accessing expired document raises error."""
        store = DocumentStore(max_documents=10, ttl=1)
        sections = [Section(id="art_1", title="Art. 1.", level=2, start_pos=0)]
        await store.load("DU/2024/1", "Test content", sections)

        time.sleep(1.5)

        with pytest.raises(DocumentNotLoadedError, match="Dokument DU/2024/1 nie jest załadowany"):
            await store.get_section("DU/2024/1", "art_1")

    async def test_access_updates_last_accessed(self):
        """Test that accessing a document updates last_accessed time."""
        store = DocumentStore(max_documents=10, ttl=5)
        sections = [Section(id="art_1", title="Art. 1.", level=2, start_pos=0)]
        await store.load("DU/2024/1", "Test content", sections)

        time.sleep(1)
        await store.get_toc("DU/2024/1")  # Access the document
        time.sleep(1)

        # Should still be loaded because last_accessed was updated
        assert await store.is_loaded("DU/2024/1")


@pytest.mark.asyncio
class TestLRUEviction:
    """Tests for LRU eviction when max_documents is reached."""

    async def test_lru_eviction_when_full(self):
        """Test that LRU document is evicted when store is full."""
        store = DocumentStore(max_documents=3)
        sections = [Section(id="art_1", title="Art. 1.", level=2, start_pos=0)]

        # Load 3 documents to fill the store
        for i in range(3):
            await store.load(f"DU/2024/{i + 1}", f"Content {i + 1}", sections)

        assert await store.is_loaded("DU/2024/1")

        # Load one more to trigger eviction
        time.sleep(0.1)  # Ensure different timestamps
        await store.load("DU/2024/4", "Content 4", sections)

        # The oldest document (DU/2024/1) should be evicted
        assert not await store.is_loaded("DU/2024/1")
        assert await store.is_loaded("DU/2024/4")

    async def test_lru_evicts_least_recently_accessed(self):
        """Test that least recently accessed document is evicted."""
        store = DocumentStore(max_documents=3, ttl=3600)
        sections = [Section(id="art_1", title="Art. 1.", level=2, start_pos=0)]

        # Load 3 documents
        for i in range(3):
            await store.load(f"DU/2024/{i + 1}", f"Content {i + 1}", sections)
            time.sleep(0.1)

        # Access document 1 and 2, but not 3
        await store.get_toc("DU/2024/1")
        time.sleep(0.1)
        await store.get_toc("DU/2024/2")
        time.sleep(0.1)

        # Load one more document
        await store.load("DU/2024/4", "Content 4", sections)

        # Document 3 (least recently accessed) should be evicted
        assert not await store.is_loaded("DU/2024/3")
        assert await store.is_loaded("DU/2024/1")
        assert await store.is_loaded("DU/2024/2")
        assert await store.is_loaded("DU/2024/4")


@pytest.mark.asyncio
class TestDocumentSizeLimits:
    """Tests for document size limits."""

    async def test_oversized_document_is_refused_not_truncated(self):
        """An act cut mid-clause is a silent loss; the store must refuse it.

        `ActService` already rejects oversized acts before they reach the
        store, so this path guards future callers that skip that check. The
        limit is measured in UTF-8 bytes, not characters, so a document that
        fits by `len()` can still exceed it.
        """
        store = DocumentStore(max_size_bytes=100)
        large_content = "ż" * 60  # 60 characters, 120 bytes in UTF-8
        sections = [Section(id="art_1", title="Art. 1.", level=2, start_pos=0)]

        with pytest.raises(ContentTooLargeError) as excinfo:
            await store.load("DU/2024/1", large_content, sections)

        message = str(excinfo.value)
        assert "DU/2024/1" in message
        assert "przekracza limit 100 B" in message
        assert excinfo.value.size_bytes == 120
        assert excinfo.value.limit_bytes == 100
        assert not await store.is_loaded("DU/2024/1")

    async def test_normal_size_document_not_truncated(self):
        """Test that documents within size limits are not truncated."""
        store = DocumentStore(max_size_bytes=1000)
        content = "Test content within limits"
        sections = [
            Section(
                id="art_1",
                title="Art. 1.",
                level=2,
                start_pos=0,
                content=content,
            ),
        ]

        await store.load("DU/2024/1", content, sections)

        section_content = await store.get_section("DU/2024/1", "art_1")
        assert section_content == content


class TestLoadedDocument:
    """Tests for LoadedDocument dataclass."""

    def test_loaded_document_creation(self):
        """Test creating a LoadedDocument."""
        sections = [Section(id="art_1", title="Art. 1.", level=2, start_pos=0)]
        doc = LoadedDocument(
            eli="DU/2024/1",
            markdown="Test content",
            sections=sections,
        )

        assert doc.eli == "DU/2024/1"
        assert doc.markdown == "Test content"
        assert len(doc.sections) == 1
        assert doc.size_bytes > 0

    def test_loaded_document_size_calculation(self):
        """Test that document size is calculated correctly."""
        content = "Test content"
        doc = LoadedDocument(
            eli="DU/2024/1",
            markdown=content,
            sections=[],
        )

        expected_size = len(content.encode("utf-8"))
        assert doc.size_bytes == expected_size

    def test_loaded_document_timestamps(self):
        """Test that timestamps are set on creation."""
        import time

        before = time.time()
        doc = LoadedDocument(
            eli="DU/2024/1",
            markdown="Test",
            sections=[],
        )
        after = time.time()

        assert before <= doc.loaded_at <= after
        assert before <= doc.last_accessed <= after


@pytest.mark.asyncio
class TestEdgeCases:
    """Tests for edge cases."""

    async def test_empty_sections_list(self, document_store: DocumentStore):
        """Test loading document with no sections."""
        await document_store.load("DU/2024/1", "Content without sections", [])
        assert await document_store.is_loaded("DU/2024/1")
        toc = await document_store.get_toc("DU/2024/1")
        assert toc == []

    async def test_empty_markdown(self, document_store: DocumentStore):
        """Test loading document with empty markdown."""
        sections = [Section(id="art_1", title="Art. 1.", level=2, start_pos=0)]
        await document_store.load("DU/2024/1", "", sections)
        assert await document_store.is_loaded("DU/2024/1")

    async def test_reload_same_document(self, document_store: DocumentStore):
        """Test reloading the same document overwrites it."""
        sections1 = [Section(id="art_1", title="Art. 1.", level=2, start_pos=0)]
        sections2 = [
            Section(id="art_1", title="Art. 1.", level=2, start_pos=0),
            Section(id="art_2", title="Art. 2.", level=2, start_pos=50),
        ]

        await document_store.load("DU/2024/1", "First version", sections1)
        assert len(await document_store.get_toc("DU/2024/1")) == 1

        await document_store.load("DU/2024/1", "Second version", sections2)
        assert len(await document_store.get_toc("DU/2024/1")) == 2


@pytest.mark.asyncio
class TestSearchTreatsQueryLiterally:
    """Regression guard — `query` must not be treated as a raw pattern."""

    async def test_catastrophic_pattern_is_treated_as_literal_text(self, document_store: DocumentStore):
        """The catastrophic pattern is INSIDE the document text; the assertion is
        on the hit position — not merely on a miss. A regression that drops
        `re.escape` then fails immediately on the wrong position instead of
        hanging the run: `pytest.mark.timeout` cannot protect this case because
        `re` does not release the GIL during backtracking, so the timer thread
        never gets scheduled."""
        markdown = "Art. 1. Zabroniony wzorzec (.+)+! jest tu zwykłym tekstem!"
        sections = [Section(id="art_1", title="Art. 1.", level=2, start_pos=0)]
        await document_store.load("DU/2024/1", markdown, sections)

        hits = await _search(document_store, "DU/2024/1", "(.+)+!")

        assert len(hits) == 1
        assert hits[0].match_start == markdown.index("(.+)+!")

    async def test_regex_metacharacters_match_only_literally(self, document_store: DocumentStore):
        """Regex metacharacters ('(', '.') in query must act as literal characters —
        the group and the dot must not be interpreted as a pattern."""
        markdown = "Art. 1. Stawka wynosi 23% (dwadzieścia trzy procent)."
        sections = [Section(id="art_1", title="Art. 1.", level=2, start_pos=0)]
        await document_store.load("DU/2024/1", markdown, sections)

        literal_hits = await _search(document_store, "DU/2024/1", "(dwadzieścia")
        wildcard_hits = await _search(document_store, "DU/2024/1", "23.")

        assert len(literal_hits) == 1
        assert wildcard_hits == []  # dot is not a metacharacter — "23." does not occur


class _CountingStarts(Sequence[int]):
    """A sequence that records how many element reads bisect performed."""

    def __init__(self, values: list[int]) -> None:
        self._values = values
        self.reads = 0

    @overload
    def __getitem__(self, index: int) -> int: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[int]: ...

    def __getitem__(self, index: int | slice) -> int | Sequence[int]:
        self.reads += 1
        return self._values[index]

    def __len__(self) -> int:
        return len(self._values)


def _numbered_sections(count: int, *, span: int = 100, offset: int = 0) -> list[Section]:
    return [
        Section(
            id=f"art_{n}",
            title=f"Art. {n}",
            level=2,
            start_pos=offset + n * span,
            end_pos=offset + (n + 1) * span,
            content="x",
        )
        for n in range(count)
    ]


class TestSectionForPosition:
    def test_returns_the_section_containing_the_position(self) -> None:
        sections = _numbered_sections(5)
        starts = [section.start_pos for section in sections]

        assert section_for_position(starts, sections, 250, 500) == ("art_2", "Art. 2")

    def test_position_before_the_first_section_is_unknown(self) -> None:
        sections = _numbered_sections(3, span=100, offset=50)
        starts = [section.start_pos for section in sections]

        assert section_for_position(starts, sections, 10, 400) == UNKNOWN_SECTION

    def test_position_in_a_gap_after_a_section_end_is_unknown(self) -> None:
        sections = [Section(id="art_1", title="Art. 1", level=2, start_pos=0, end_pos=10)]

        assert section_for_position([0], sections, 50, 100) == UNKNOWN_SECTION

    def test_open_ended_last_section_extends_to_the_document_end(self) -> None:
        sections = [Section(id="art_1", title="Art. 1", level=2, start_pos=0, end_pos=None)]

        assert section_for_position([0], sections, 90, 100) == ("art_1", "Art. 1")

    def test_explicit_zero_end_pos_is_not_confused_with_open_ended(self) -> None:
        """Only `None` means "extends to the end"; `end_pos=0` is an empty range.

        Written as `end_pos or document_length`, the falsy zero silently
        turned an empty section into one covering the whole document.
        """
        sections = [Section(id="art_1", title="Art. 1", level=2, start_pos=0, end_pos=0)]

        assert section_for_position([0], sections, 5, 100) == UNKNOWN_SECTION

    def test_section_lookup_is_logarithmic_in_the_number_of_sections(self) -> None:
        sections = _numbered_sections(4096)
        starts = _CountingStarts([section.start_pos for section in sections])

        section_for_position(starts, sections, 4095 * 100 + 1, 4096 * 100)

        assert 1 <= starts.reads <= 13, f"expected a logarithmic probe count, got {starts.reads}"


class TestLoadedDocumentSectionIndex:
    def test_section_starts_are_derived_from_the_sections(self) -> None:
        sections = _numbered_sections(3)

        document = LoadedDocument(eli="DU/2024/1", markdown="x" * 300, sections=sections)

        assert document.section_starts == (0, 100, 200)

    def test_sections_are_ordered_by_start_position(self) -> None:
        sections = list(reversed(_numbered_sections(3)))

        document = LoadedDocument(eli="DU/2024/1", markdown="x" * 300, sections=sections)

        assert [section.id for section in document.sections] == ["art_0", "art_1", "art_2"]
        assert document.section_starts == (0, 100, 200)

    def test_overlapping_sections_are_rejected_at_load_time(self) -> None:
        """`section_for_position` assumes disjoint ranges; a producer that
        breaks the assumption must fail loudly here, not attribute hits to the
        wrong section later."""
        sections = [
            Section(id="art_1", title="Art. 1", level=2, start_pos=0, end_pos=150),
            Section(id="art_2", title="Art. 2", level=2, start_pos=100, end_pos=200),
        ]

        with pytest.raises(ValueError, match=r"DU/2024/1.*Art\. 1.*150.*Art\. 2.*100"):
            LoadedDocument(eli="DU/2024/1", markdown="x" * 300, sections=sections)

    def test_open_ended_sections_do_not_trip_the_overlap_check(self) -> None:
        sections = [
            Section(id="art_1", title="Art. 1", level=2, start_pos=0, end_pos=None),
            Section(id="art_2", title="Art. 2", level=2, start_pos=100, end_pos=None),
        ]

        document = LoadedDocument(eli="DU/2024/1", markdown="x" * 300, sections=sections)

        assert document.section_starts == (0, 100)


def _document_with_two_hits() -> tuple[str, list[Section]]:
    markdown = "Art. 1\npodatek od nieruchomosci\nArt. 2\npodatek dochodowy\n"
    sections = [
        Section(id="art_1", title="Art. 1", level=2, start_pos=0, end_pos=32),
        Section(id="art_2", title="Art. 2", level=2, start_pos=32, end_pos=len(markdown)),
    ]
    return markdown, sections


@pytest.mark.asyncio
class TestScanAndHydrate:
    async def test_scan_returns_only_match_positions(self) -> None:
        markdown, sections = _document_with_two_hits()
        store = DocumentStore()
        await store.load("DU/2024/1", markdown, sections)

        spans = await store.scan("DU/2024/1", "podatek")

        assert spans == [(7, 14), (39, 46)]

    async def test_scan_is_case_insensitive_and_escapes_the_query(self) -> None:
        store = DocumentStore()
        await store.load("DU/2024/1", "Art. 1 (a+b) i A+B", [])

        assert await store.scan("DU/2024/1", "a+b") == [(8, 11), (15, 18)]

    async def test_hydrate_builds_hits_only_for_the_given_spans(self) -> None:
        markdown, sections = _document_with_two_hits()
        store = DocumentStore()
        await store.load("DU/2024/1", markdown, sections)

        hits = await store.hydrate("DU/2024/1", [(39, 46)], context_chars=5)

        assert len(hits) == 1
        assert hits[0].section_id == "art_2"
        assert hits[0].section_title == "Art. 2"
        assert hits[0].match_start == 39
        assert hits[0].match_end == 46
        assert hits[0].context == markdown[34:51]

    async def test_lock_is_released_before_hits_are_built(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from typing import Any

        markdown, sections = _document_with_two_hits()
        store = DocumentStore()
        await store.load("DU/2024/1", markdown, sections)
        observed: list[bool] = []
        from law_scrapper_mcp.services import document_store as document_store_module

        original = document_store_module._build_hits

        def _spy(*args: Any, **kwargs: Any) -> Any:
            observed.append(store._lock.locked())
            return original(*args, **kwargs)

        monkeypatch.setattr(document_store_module, "_build_hits", _spy)

        await store.hydrate("DU/2024/1", [(7, 14)], context_chars=5)

        assert observed == [False], "the store lock must not be held while contexts are built"

    async def test_scan_then_hydrate_returns_every_hit(self) -> None:
        markdown, sections = _document_with_two_hits()
        store = DocumentStore()
        await store.load("DU/2024/1", markdown, sections)

        hits = await _search(store, "DU/2024/1", "podatek", context_chars=5)

        assert [hit.section_id for hit in hits] == ["art_1", "art_2"]

    async def test_scan_releases_the_lock_before_matching(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import re as re_module
        from typing import Any

        store = DocumentStore()
        await store.load("DU/2024/1", "podatek " * 100, [])
        observed: list[bool] = []
        original_compile = re_module.compile

        def _spy_compile(*args: Any, **kwargs: Any) -> Any:
            # Check if lock is held when re.compile is called.
            # This happens after scan() has released the lock.
            observed.append(store._lock.locked())
            return original_compile(*args, **kwargs)

        monkeypatch.setattr(re_module, "compile", _spy_compile)

        await store.scan("DU/2024/1", "podatek")

        assert observed == [False], "the store lock must not be held during pattern compilation"

    async def test_hydrate_silently_drops_out_of_bounds_spans(self) -> None:
        markdown, sections = _document_with_two_hits()
        store = DocumentStore()
        await store.load("DU/2024/1", markdown, sections)

        # Reload with shorter content (simulating document mutation)
        new_markdown = "Art. 1\npodatek\n"
        await store.load("DU/2024/1", new_markdown, sections[:1])

        # The old spans (39, 46) are now out of bounds (document is only 14 chars)
        hits = await store.hydrate("DU/2024/1", [(7, 14), (39, 46)], context_chars=5)

        # Only the in-bounds span should produce a hit
        assert len(hits) == 1
        assert hits[0].match_start == 7
        assert hits[0].match_end == 14
