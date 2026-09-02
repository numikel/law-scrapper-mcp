"""In-memory document store for loaded legal acts with section-level access."""

import asyncio
import logging
import re
import time
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise

from law_scrapper_mcp.client.exceptions import ContentTooLargeError, DocumentNotLoadedError
from law_scrapper_mcp.models.pagination import DEFAULT_ITEM_LIMIT, MAX_ITEM_LIMIT
from law_scrapper_mcp.models.tool_outputs import LoadedDocumentInfo, LoadedDocumentListOutput
from law_scrapper_mcp.services.content_processor import Section
from law_scrapper_mcp.services.pagination import effective_limit, paginate_items, parse_non_negative

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
        # `section_for_position` relies on disjoint ranges; a producer that
        # violates that would misattribute hits silently, so refuse it here.
        for current, following in pairwise(self.sections):
            if current.end_pos is not None and current.end_pos > following.start_pos:
                raise ValueError(
                    f"Sekcje aktu {self.eli} nakładają się: '{current.title}' kończy się na pozycji "
                    f"{current.end_pos}, a '{following.title}' zaczyna się na pozycji {following.start_pos}."
                )
        self.section_starts = tuple(section.start_pos for section in self.sections)


def section_for_position(
    section_starts: Sequence[int],
    sections: Sequence[Section],
    position: int,
    document_length: int,
) -> tuple[str, str]:
    """Return the (id, title) of the section covering `position`.

    Precondition: `sections` is sorted by `start_pos` and the ranges are
    disjoint, so the candidate is the last section starting at or before
    `position`. `ContentProcessor.index_sections` guarantees this — each
    section ends where the next begins — and `LoadedDocument.__post_init__`
    rejects any producer that does not. A linear scan over `sections` would
    make in-act search cost matches x sections; this is logarithmic.

    Only `end_pos is None` means "extends to the end of the document"; an
    explicit `0` is an empty range, not an open-ended one.
    """
    index = bisect_right(section_starts, position) - 1
    if index < 0:
        return UNKNOWN_SECTION
    section = sections[index]
    end = section.end_pos if section.end_pos is not None else document_length
    if position < end:
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
    """In-memory store for loaded legal acts with section-level access.

    Invariant, load-bearing: **no critical section of this class may contain an
    `await`**. Because none does, the lock is never held across a suspension
    point, so an uncontended `Lock.acquire()` never yields and the
    scan_page/hydrate sequence in `ContentService.search` is atomic with
    respect to the event loop. That is the only reason
    `page_info.returned_count` cannot exceed the number of hits actually
    returned.

    Adding a suspension point inside any critical section — moving the regex
    scan to `run_in_executor`, for instance — breaks that atomicity and makes
    it possible for `hydrate` to build context from a document that replaced
    the one `scan_page` measured. The span filter guards ranges, not identity,
    so such a mismatch would be returned silently. Pair any such change with a
    document generation token checked in `hydrate`.
    """

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
        doc_size = len(markdown.encode("utf-8"))
        # `ActService` refuses an oversized act against this same limit before
        # it ever gets here, so this guards callers that skip that check. It
        # refuses rather than truncates: a legal act cut mid-clause is a loss
        # the model cannot detect, while a refusal is one it can act on. The
        # store does not know the source URL, so the error carries none.
        if doc_size > self._max_size_bytes:
            raise ContentTooLargeError(eli, doc_size, self._max_size_bytes)

        async with self._lock:
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

    async def scan_page(self, eli: str, query: str, *, limit: int, offset: int) -> tuple[list[MatchSpan], int]:
        """Return one page of literal-match positions and the exact match count.

        The document is walked once. Only spans whose match index falls in
        `[offset, offset + limit)` are kept, so memory is bounded by `limit`
        rather than by how often the query occurs — a single space in a 1 MiB
        act has over a hundred thousand matches, and a list of them all cost
        about 15 MB per request. The total still counts every match, so the
        caller's page metadata stays exact.

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
        page_end = offset + limit
        spans: list[MatchSpan] = []
        total = 0
        for index, match in enumerate(pattern.finditer(markdown)):
            total = index + 1
            if offset <= index < page_end:
                spans.append((match.start(), match.end()))
        return spans, total

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

        Spans must originate from a scan_page() of the same document. Today the
        store cannot change between the two calls — see the no-`await`
        invariant on the class — so the filter below is a guard against a
        future suspension point, not against a reachable state. Spans outside
        `0 <= start <= end <= document_length` are silently dropped rather than
        corrupting output. The bound is `<=`, not `<`: an empty query makes
        `finditer` yield one zero-width span per position, and those spans are
        legitimate results that predate the pagination work.
        """
        async with self._lock:
            doc = self._get_doc(eli)
            doc.last_accessed = time.time()
            markdown = doc.markdown
            sections = doc.sections
            section_starts = doc.section_starts

        document_length = len(markdown)
        # Filter out spans that are no longer valid for the current document.
        # This happens if the store mutated between scan_page and hydrate.
        valid_spans = [(start, end) for start, end in spans if 0 <= start <= end <= document_length]

        return _build_hits(markdown, sections, section_starts, valid_spans, context_chars)

    async def get_toc(self, eli: str) -> list[Section]:
        """Get table of contents for a loaded document."""
        async with self._lock:
            doc = self._get_doc(eli)
            doc.last_accessed = time.time()
            # A copy, not the live list: `section_starts` is an index derived
            # from it, and a caller mutating the original would desynchronise
            # the two with no signal.
            return list(doc.sections)

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

    async def list_documents_page(
        self,
        *,
        limit: str | int | None = DEFAULT_ITEM_LIMIT,
        offset: str | int | None = 0,
    ) -> LoadedDocumentListOutput:
        """Return one page of the loaded-document listing."""
        page_limit = effective_limit(limit, default=DEFAULT_ITEM_LIMIT, maximum=MAX_ITEM_LIMIT)
        page_offset = parse_non_negative(offset, name="offset", default=0)
        raw = await self.list_documents()
        entries = [LoadedDocumentInfo.model_validate(item) for item in raw]
        page, page_info = paginate_items(entries, limit=page_limit, offset=page_offset)
        return LoadedDocumentListOutput(
            documents=page,
            count=page_info.returned_count,
            page_info=page_info,
        )

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
