"""Reading loaded act content and searching inside it."""

from law_scrapper_mcp.models.pagination import (
    DEFAULT_CONTEXT_CHARS,
    DEFAULT_ITEM_LIMIT,
    DEFAULT_SECTION_CHAR_LIMIT,
    MAX_CONTEXT_CHARS,
    MAX_ITEM_LIMIT,
    MAX_SECTION_CHAR_LIMIT,
)
from law_scrapper_mcp.models.tool_outputs import ContentOutput, SearchInActOutput
from law_scrapper_mcp.services.document_store import DocumentStore
from law_scrapper_mcp.services.pagination import (
    effective_limit,
    item_page_info,
    paginate_items,
    paginate_text,
    parse_non_negative,
)

TOC_SECTION_TITLE = "Spis treści"


class ContentService:
    """Turns document store primitives into bounded tool outputs."""

    def __init__(self, document_store: DocumentStore) -> None:
        self._document_store = document_store

    async def read(
        self,
        eli: str,
        *,
        section: str | None = None,
        limit: str | int | None = None,
        offset: str | int = 0,
    ) -> ContentOutput:
        """Return one page of a section's text, or of the table of contents."""
        page_offset = parse_non_negative(offset, name="offset", default=0)
        if section is None:
            return await self._read_toc(eli, limit=limit, offset=page_offset)
        return await self._read_section(eli, section, limit=limit, offset=page_offset)

    async def _read_toc(self, eli: str, *, limit: str | int | None, offset: int) -> ContentOutput:
        page_limit = effective_limit(limit, default=DEFAULT_ITEM_LIMIT, maximum=MAX_ITEM_LIMIT)
        sections = await self._document_store.get_toc(eli)
        entries = [{"id": item.id, "title": item.title, "level": item.level} for item in sections]
        toc, page_info = paginate_items(entries, limit=page_limit, offset=offset)
        return ContentOutput(
            eli=eli,
            section_id=None,
            section_title=TOC_SECTION_TITLE,
            content=f"Znaleziono {page_info.total_count} sekcji",
            toc=toc,
            page_info=page_info,
        )

    async def _read_section(
        self,
        eli: str,
        section: str,
        *,
        limit: str | int | None,
        offset: int,
    ) -> ContentOutput:
        page_limit = effective_limit(
            limit,
            default=DEFAULT_SECTION_CHAR_LIMIT,
            maximum=MAX_SECTION_CHAR_LIMIT,
        )
        full_content = await self._document_store.get_section(eli, section)
        if full_content is None:
            raise ValueError(
                f"Sekcja '{section}' nie znaleziona w akcie {eli}. "
                f"Użyj read_act_content(eli='{eli}') aby zobaczyć dostępne sekcje."
            )
        content, page_info = paginate_text(full_content, limit=page_limit, offset=offset)
        return ContentOutput(
            eli=eli,
            section_id=section,
            section_title=section,
            content=content,
            page_info=page_info,
        )

    async def search(
        self,
        eli: str,
        query: str,
        *,
        context_chars: str | int = DEFAULT_CONTEXT_CHARS,
        limit: str | int = DEFAULT_ITEM_LIMIT,
        offset: str | int = 0,
    ) -> SearchInActOutput:
        """Return one page of in-act matches with their surrounding context.

        Scanning covers the whole document, so `total_count` stays exact, but
        the store keeps only the requested window of spans, so memory is
        bounded by `limit` rather than by the number of matches. Context
        slicing and section attribution are paid only for that window.
        """
        requested_context = parse_non_negative(context_chars, name="context_chars", default=DEFAULT_CONTEXT_CHARS)
        context_size = min(requested_context, MAX_CONTEXT_CHARS)
        page_limit = effective_limit(limit, default=DEFAULT_ITEM_LIMIT, maximum=MAX_ITEM_LIMIT)
        page_offset = parse_non_negative(offset, name="offset", default=0)

        page_spans, total = await self._document_store.scan_page(eli, query, limit=page_limit, offset=page_offset)
        page_info = item_page_info(limit=page_limit, offset=page_offset, returned=len(page_spans), total=total)
        hits = await self._document_store.hydrate(eli, page_spans, context_chars=context_size)

        matches = [
            {
                "section_id": hit.section_id,
                "section_title": hit.section_title,
                "context": hit.context,
                "position": f"{hit.match_start}-{hit.match_end}",
            }
            for hit in hits
        ]
        return SearchInActOutput(
            eli=eli,
            query=query,
            matches=matches,
            total_matches=page_info.total_count,
            page_info=page_info,
            context_chars_requested=requested_context,
            context_chars_applied=context_size,
        )
