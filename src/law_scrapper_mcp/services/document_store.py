"""In-memory document store for loaded legal acts with section-level access."""

import asyncio
import logging
import re
import time
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field

from law_scrapper_mcp.client.exceptions import DocumentNotLoadedError
from law_scrapper_mcp.services.content_processor import Section

logger = logging.getLogger(__name__)

UNKNOWN_SECTION = ("unknown", "Unknown section")
MatchSpan = tuple[int, int]


@dataclass
class LoadedDocument:
    """Represents a loaded legal document with indexed sections."""

    eli: str
    markdown: str
    sections: list[Section]
    loaded_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    size_bytes: int = 0
    section_starts: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        self.size_bytes = len(self.markdown.encode("utf-8"))
        self.sections = sorted(self.sections, key=lambda section: section.start_pos)
        self.section_starts = tuple(section.start_pos for section in self.sections)


def section_for_position(
    section_starts: Sequence[int],
    sections: Sequence[Section],
    position: int,
    document_length: int,
) -> tuple[str, str]:
    """Return the (id, title) of the section covering `position`.

    Sections occupy disjoint, increasing ranges, so the candidate is the last
    section starting at or before `position`. A linear scan over `sections`
    would make in-act search cost matches x sections; this is logarithmic.
    """
    index = bisect_right(section_starts, position) - 1
    if index < 0:
        return UNKNOWN_SECTION
    section = sections[index]
    if position < (section.end_pos or document_length):
        return section.id, section.title
    return UNKNOWN_SECTION


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
                logger.warning(f"Document {eli} exceeds max size ({doc_size} > {self._max_size_bytes}), truncating")
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
                if section.id.lower() == section_id_lower or section.title.lower().startswith(section_id.lower()):
                    return section.content

            # Try matching by "Art. X" pattern
            art_match = re.match(r"art\.?\s*(\d+[a-z]?)", section_id, re.IGNORECASE)
            if art_match:
                art_num = art_match.group(1)
                for section in doc.sections:
                    if re.match(rf"Art\.?\s*{re.escape(art_num)}", section.title, re.IGNORECASE):
                        return section.content

            return None

    async def scan(self, eli: str, query: str) -> list[MatchSpan]:
        """Return the positions of every literal match, without building context.

        The query is escaped before compilation, so Python's backtracking
        engine never receives client-supplied regex syntax. Replacing
        ``re.escape(query)`` with ``query`` would reintroduce the ReDoS risk
        addressed by the pattern filtering path.

        The lock is held only long enough to resolve the document and copy the
        markdown reference; it is released before the pattern matching runs.
        Other coroutines waiting on this lock are not blocked by the scan
        itself. Note: the scan is still synchronous CPU-bound work with no
        ``await`` points, so it occupies the event loop for its duration and
        may delay unrelated coroutines — only the lock duration is reduced,
        not the total event-loop occupancy.
        """
        async with self._lock:
            doc = self._get_doc(eli)
            doc.last_accessed = time.time()
            markdown = doc.markdown

        pattern = re.compile(re.escape(query), re.IGNORECASE)
        return [(match.start(), match.end()) for match in pattern.finditer(markdown)]

    async def hydrate(
        self,
        eli: str,
        spans: Sequence[MatchSpan],
        *,
        context_chars: int,
    ) -> list[SearchHit]:
        """Build full hits for the given spans only.

        Cost is proportional to `len(spans)`, not to the number of matches in
        the document. The lock is released before any context extraction.

        Spans must originate from a scan() of the same document; the store may
        have changed between calls (document expired, reloaded). Spans that
        extend beyond the current document (0 <= start < end <= document_length)
        are silently dropped rather than corrupting output.
        """
        async with self._lock:
            doc = self._get_doc(eli)
            doc.last_accessed = time.time()
            markdown = doc.markdown
            sections = doc.sections
            section_starts = doc.section_starts

        document_length = len(markdown)
        # Filter out spans that are no longer valid for the current document.
        # This happens if the store mutated between scan and hydrate.
        valid_spans = [(start, end) for start, end in spans if start >= 0 and end <= document_length]

        return _build_hits(markdown, sections, section_starts, valid_spans, context_chars)

    async def search(self, eli: str, query: str, context_chars: int = 500) -> list[SearchHit]:
        """Search literal text within a loaded document, returning every hit.

        Kept as the unbounded composition of `scan` and `hydrate`. Callers that
        only need one page must paginate the spans between the two calls.
        """
        spans = await self.scan(eli, query)
        return await self.hydrate(eli, spans, context_chars=context_chars)

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
                    "loaded_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(doc.loaded_at)),
                    "last_accessed": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(doc.last_accessed)),
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


def _build_hits(
    markdown: str,
    sections: Sequence[Section],
    section_starts: Sequence[int],
    spans: Sequence[MatchSpan],
    context_chars: int,
) -> list[SearchHit]:
    """Turn match spans into hits with context and section attribution."""
    document_length = len(markdown)
    hits: list[SearchHit] = []
    for match_start, match_end in spans:
        start = max(0, match_start - context_chars)
        end = min(document_length, match_end + context_chars)
        section_id, section_title = section_for_position(section_starts, sections, match_start, document_length)
        hits.append(
            SearchHit(
                section_id=section_id,
                section_title=section_title,
                context=markdown[start:end],
                match_start=match_start,
                match_end=match_end,
            )
        )
    return hits
