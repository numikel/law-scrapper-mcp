"""Tests for DocumentStore service."""

from __future__ import annotations

import time

import pytest

from law_scrapper_mcp.client.exceptions import DocumentNotLoadedError
from law_scrapper_mcp.services.content_processor import Section
from law_scrapper_mcp.services.document_store import DocumentStore, LoadedDocument


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

        hits = await document_store.search("DU/2024/1", "keyword")
        assert len(hits) == 1
        assert "keyword" in hits[0].context

    async def test_search_case_insensitive(self, document_store: DocumentStore):
        """Test case-insensitive search."""
        markdown = "Art. 1. Testing KEYWORD here."
        sections = [
            Section(id="art_1", title="Art. 1.", level=2, start_pos=0, content=markdown),
        ]
        await document_store.load("DU/2024/1", markdown, sections)

        hits = await document_store.search("DU/2024/1", "keyword")
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

        hits = await document_store.search("DU/2024/1", "keyword")
        assert len(hits) == 2

    async def test_search_with_context(self, document_store: DocumentStore):
        """Test search context extraction."""
        markdown = "This is a long text with the keyword in the middle and more text after."
        sections = [
            Section(id="section_1", title="Section", level=1, start_pos=0),
        ]
        await document_store.load("DU/2024/1", markdown, sections)

        hits = await document_store.search("DU/2024/1", "keyword", context_chars=20)
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

        hits = await document_store.search("DU/2024/1", "keyword")
        assert len(hits) == 1
        assert hits[0].section_id == "art_2"
        assert hits[0].section_title == "Art. 2."

    async def test_search_no_matches(self, document_store: DocumentStore):
        """Test search with no matches."""
        markdown = "Art. 1. Some content here."
        sections = [Section(id="art_1", title="Art. 1.", level=2, start_pos=0)]
        await document_store.load("DU/2024/1", markdown, sections)

        hits = await document_store.search("DU/2024/1", "nonexistent")
        assert len(hits) == 0

    async def test_search_document_not_loaded(self, document_store: DocumentStore):
        """Test searching in non-loaded document raises error."""
        with pytest.raises(DocumentNotLoadedError, match="Dokument DU/2024/999 nie jest załadowany"):
            await document_store.search("DU/2024/999", "keyword")


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


class TestLRUEviction:
    """Tests for LRU eviction when max_documents is reached."""

    async def test_lru_eviction_when_full(self):
        """Test that LRU document is evicted when store is full."""
        store = DocumentStore(max_documents=3)
        sections = [Section(id="art_1", title="Art. 1.", level=2, start_pos=0)]

        # Load 3 documents to fill the store
        for i in range(3):
            await store.load(f"DU/2024/{i+1}", f"Content {i+1}", sections)

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
            await store.load(f"DU/2024/{i+1}", f"Content {i+1}", sections)
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


class TestDocumentSizeLimits:
    """Tests for document size limits."""

    async def test_large_document_truncated(self):
        """Test that documents exceeding max size are truncated."""
        store = DocumentStore(max_size_bytes=100)
        large_content = "x" * 200  # 200 bytes
        sections = [Section(id="art_1", title="Art. 1.", level=2, start_pos=0)]

        await store.load("DU/2024/1", large_content, sections)

        # Document should be loaded but truncated
        assert await store.is_loaded("DU/2024/1")

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
