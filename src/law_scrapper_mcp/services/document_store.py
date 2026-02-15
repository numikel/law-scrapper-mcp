"""In-memory document store for loaded legal acts with section-level access."""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

from law_scrapper_mcp.client.exceptions import DocumentNotLoadedError
from law_scrapper_mcp.services.content_processor import Section

logger = logging.getLogger(__name__)


@dataclass
class LoadedDocument:
    """Represents a loaded legal document with indexed sections."""

    eli: str
    markdown: str
    sections: list[Section]
    loaded_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    size_bytes: int = 0

    def __post_init__(self):
        self.size_bytes = len(self.markdown.encode("utf-8"))


@dataclass
class SearchHit:
    """Represents a search result within a document."""

    section_id: str
    section_title: str
    context: str
    match_start: int
    match_end: int


class DocumentStore:
    """In-memory store for loaded legal acts with section-level access."""

    def __init__(
        self,
        max_documents: int = 10,
        max_size_bytes: int = 5 * 1024 * 1024,
        ttl: int = 7200,
    ):
        self._store: dict[str, LoadedDocument] = {}
        self._max_documents = max_documents
        self._max_size_bytes = max_size_bytes
        self._ttl = ttl
        self._lock = asyncio.Lock()

    async def load(self, eli: str, markdown: str, sections: list[Section]) -> None:
        """Load a document into the store."""
        async with self._lock:
            doc_size = len(markdown.encode("utf-8"))
            if doc_size > self._max_size_bytes:
                logger.warning(
                    f"Document {eli} exceeds max size ({doc_size} > {self._max_size_bytes}), truncating"
                )
                # Truncate to max size
                markdown = markdown[: self._max_size_bytes]
                # Re-index sections for truncated content
                sections = [s for s in sections if s.start_pos < len(markdown)]

            self._evict_expired()

            if len(self._store) >= self._max_documents and eli not in self._store:
                self._evict_lru()

            self._store[eli] = LoadedDocument(eli=eli, markdown=markdown, sections=sections)
            logger.info(f"Loaded document {eli} ({doc_size} bytes, {len(sections)} sections)")

    async def get_section(self, eli: str, section_id: str) -> str | None:
        """Get content of a specific section."""
        async with self._lock:
            doc = self._get_doc(eli)
            doc.last_accessed = time.time()

            # Find section by ID (case-insensitive, flexible matching)
            section_id_lower = section_id.lower().replace(" ", "_")
            for section in doc.sections:
                if (
                    section.id.lower() == section_id_lower
                    or section.title.lower().startswith(section_id.lower())
                ):
                    return section.content

            # Try matching by "Art. X" pattern
            art_match = re.match(r"art\.?\s*(\d+[a-z]?)", section_id, re.IGNORECASE)
            if art_match:
                art_num = art_match.group(1)
                for section in doc.sections:
                    if re.match(
                        rf"Art\.?\s*{re.escape(art_num)}", section.title, re.IGNORECASE
                    ):
                        return section.content

            return None

    async def search(self, eli: str, query: str, context_chars: int = 500) -> list[SearchHit]:
        """Search within a loaded document."""
        async with self._lock:
            doc = self._get_doc(eli)
            doc.last_accessed = time.time()

            hits = []
            try:
                pattern = re.compile(re.escape(query), re.IGNORECASE)
            except re.error:
                pattern = re.compile(re.escape(query), re.IGNORECASE)

            for match in pattern.finditer(doc.markdown):
                start = max(0, match.start() - context_chars)
                end = min(len(doc.markdown), match.end() + context_chars)
                context = doc.markdown[start:end]

                # Find which section this match belongs to
                section_id = "unknown"
                section_title = "Unknown section"
                for section in doc.sections:
                    if section.start_pos <= match.start() < (
                        section.end_pos or len(doc.markdown)
                    ):
                        section_id = section.id
                        section_title = section.title
                        break

                hits.append(
                    SearchHit(
                        section_id=section_id,
                        section_title=section_title,
                        context=context,
                        match_start=match.start(),
                        match_end=match.end(),
                    )
                )

            return hits

    async def get_toc(self, eli: str) -> list[Section]:
        """Get table of contents for a loaded document."""
        async with self._lock:
            doc = self._get_doc(eli)
            doc.last_accessed = time.time()
            return doc.sections

    async def is_loaded(self, eli: str) -> bool:
        """Check if a document is loaded."""
        async with self._lock:
            if eli not in self._store:
                return False
            doc = self._store[eli]
            if time.time() - doc.last_accessed > self._ttl:
                del self._store[eli]
                return False
            return True

    async def list_documents(self) -> list[dict[str, object]]:
        """List all loaded documents with metadata."""
        async with self._lock:
            self._evict_expired()
            return [
                {
                    "eli": doc.eli,
                    "size_bytes": doc.size_bytes,
                    "section_count": len(doc.sections),
                    "loaded_at": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(doc.loaded_at)
                    ),
                    "last_accessed": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(doc.last_accessed)
                    ),
                }
                for doc in self._store.values()
            ]

    async def evict(self, eli: str) -> None:
        """Manually evict a document."""
        async with self._lock:
            self._store.pop(eli, None)

    def _get_doc(self, eli: str) -> LoadedDocument:
        """Get document or raise error (must be called under lock)."""
        if eli not in self._store:
            raise DocumentNotLoadedError(eli)
        doc = self._store[eli]
        if time.time() - doc.last_accessed > self._ttl:
            del self._store[eli]
            raise DocumentNotLoadedError(eli)
        return doc

    def _evict_expired(self) -> None:
        """Remove expired documents (called under lock)."""
        now = time.time()
        expired = [k for k, v in self._store.items() if now - v.last_accessed > self._ttl]
        for key in expired:
            del self._store[key]

    def _evict_lru(self) -> None:
        """Remove least recently used document (called under lock)."""
        if not self._store:
            return
        lru_key = min(self._store, key=lambda k: self._store[k].last_accessed)
        logger.info(f"Evicting LRU document: {lru_key}")
        del self._store[lru_key]
