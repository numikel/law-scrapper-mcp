"""Act details service with content loading."""

import asyncio
import logging
from typing import Any

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.exceptions import (
    ActNotFoundError,
    ContentNotAvailableError,
    ContentTooLargeError,
    ResponseTooLargeError,
)
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.config import settings
from law_scrapper_mcp.models.tool_inputs import parse_eli
from law_scrapper_mcp.models.tool_outputs import ActDetailOutput, ContentStatus
from law_scrapper_mcp.services.content_processor import ContentProcessor
from law_scrapper_mcp.services.document_store import DocumentStore

logger = logging.getLogger(__name__)


def _reject_if_too_large(eli: str, size_bytes: int, limit_bytes: int, pdf_url: str) -> None:
    """Refuse conversion before it starts.

    The limit lands *before* the converter, not after. `index_sections` used to
    run on uncapped markdown, so `doc_store_max_size_bytes` bounded what was
    stored but never what was processed.
    """
    if size_bytes > limit_bytes:
        raise ContentTooLargeError(eli, size_bytes, limit_bytes, pdf_url)


class ActService:
    """Service for retrieving act details and loading content."""

    def __init__(
        self,
        client: SejmApiClient,
        document_store: DocumentStore,
        content_processor: ContentProcessor,
    ):
        self._client = client
        self._doc_store = document_store
        self._content_processor = content_processor
        # ELIs whose text the source has already said it cannot supply. The
        # placeholder document D3 removed used to double as a negative cache;
        # without one, every repeated `load_content=True` on a textless act would
        # spend one or two upstream requests on an answer that cannot change
        # before the metadata it was derived from expires (O1). Hence the same
        # TTL value as that metadata, whose `has_html`/`has_pdf` route the fetch.
        # Same duration, not same clock: this entry starts when the load fails,
        # which can be later than the metadata was cached, so a verdict may
        # outlive its snapshot by up to one more TTL window before a re-check.
        self._known_unavailable = TTLCache(max_entries=settings.cache_max_entries)

    async def get_details(self, eli: str, load_content: bool = False) -> ActDetailOutput:
        """Get act details, optionally loading content into document store."""
        publisher, year, pos = parse_eli(eli)

        # Get act details
        data = await self._client.get_json(f"acts/{publisher}/{year}/{pos}", cache_ttl=settings.cache_details_ttl)

        # Get structure/TOC
        toc_data = []
        try:
            toc_data = await self._client.get_json(
                f"acts/{publisher}/{year}/{pos}/struct", cache_ttl=settings.cache_details_ttl
            )
        except Exception as e:
            logger.debug(f"No structure available for {eli}: {e}")

        has_html = bool(data.get("textHTML"))
        has_pdf = bool(data.get("textPDF"))

        is_loaded = await self._doc_store.is_loaded(eli)
        if load_content and not is_loaded and await self._known_unavailable.get(eli) is None:
            try:
                await self._load_content(eli, publisher, year, pos, has_html=has_html, has_pdf=has_pdf)
            except ContentNotAvailableError as exc:
                # A permanent absence is information, not a failure: the metadata
                # this call already fetched stays useful, and `content_status`
                # below says plainly that there is no text to read (D6).
                logger.info("No readable content for %s: %s", eli, exc)
                await self._known_unavailable.set(eli, True, settings.cache_details_ttl)
            else:
                is_loaded = await self._doc_store.is_loaded(eli)
        if is_loaded:
            content_status = ContentStatus.LOADED
        elif load_content:
            content_status = ContentStatus.UNAVAILABLE
        else:
            content_status = ContentStatus.NOT_REQUESTED

        return ActDetailOutput(
            eli=data.get("ELI", eli),
            publisher=data.get("publisher", publisher),
            year=data.get("year", year),
            pos=data.get("pos", pos),
            title=data.get("title", ""),
            status=data.get("status", ""),
            type=data.get("type"),
            announcement_date=data.get("announcementDate"),
            promulgation_date=data.get("promulgation"),
            entry_into_force=data.get("entryIntoForce"),
            valid_from=data.get("validFrom"),
            repeal_date=data.get("repealDate"),
            change_date=data.get("changeDate"),
            keywords=data.get("keywords", []),
            references=data.get("references"),
            volume=data.get("volume"),
            has_pdf=has_pdf,
            has_html=has_html,
            toc=self._format_toc(toc_data) if toc_data else [],
            is_loaded=is_loaded,
            content_status=content_status,
        )

    async def _load_content(
        self, eli: str, publisher: str, year: int, pos: int, *, has_html: bool, has_pdf: bool
    ) -> None:
        """Load act content into the document store.

        Failure is never silent. A transient upstream problem — an open breaker, a
        timeout, a 5xx, a converter blowing up — propagates unchanged so the tool
        layer reports `isError=true`; the caller can then retry deliberately, which
        is the only retry this project performs above `client/failure_policy.py` (O1).
        The previous `except Exception: logger.error(...)` made every one of those
        indistinguishable from an act that simply has no text.

        A permanently empty extraction — whichever format supplied it — raises
        `ContentNotAvailableError`, as does no format being available at all and
        a 404 on the fetch of either format: that is the source itself saying the
        file is not there (D5, D6).
        """
        pdf_url = f"{self._client.BASE_URL}/acts/{publisher}/{year}/{pos}/text.pdf"
        limit = settings.doc_store_max_size_bytes
        if not has_html and not has_pdf:
            # Metadata already says there is nothing to fetch. Asking anyway would
            # spend a request on a guaranteed 404 against a public state API (O1).
            raise ContentNotAvailableError(eli, "html/pdf")
        try:
            # The same limit reaches the download itself (#19): the client aborts a
            # body that runs past it while it is still streaming, so an oversized act
            # never sits whole in memory. The post-hoc gates below stay as belt and
            # braces — they bound what the converter sees, which streaming does not.
            if has_html:
                try:
                    html = await self._client.get_act_html(publisher, year, pos, max_bytes=limit)
                except ActNotFoundError as exc:
                    # 404 is the source itself saying the file is not there. Every
                    # other client error keeps its own meaning and propagates (D5).
                    raise ContentNotAvailableError(eli, "html") from exc
                _reject_if_too_large(eli, len(html.encode("utf-8")), limit, pdf_url)
                # markdownify, pdfplumber and the section regex are synchronous
                # CPU-bound work. Left in the coroutine they hold the event loop
                # for seconds, `/health` included. The offload stops at
                # `ContentProcessor`: `DocumentStore` relies on the absence of
                # `await` in its critical sections (see its class docstring).
                markdown = await asyncio.to_thread(self._content_processor.html_to_markdown, html)
                if not markdown.strip():
                    raise ContentNotAvailableError(eli, "html")
            else:
                try:
                    pdf_bytes = await self._client.get_bytes(f"acts/{publisher}/{year}/{pos}/text.pdf", max_bytes=limit)
                except ActNotFoundError as exc:
                    # 404 is the source itself saying the file is not there. Every
                    # other client error keeps its own meaning and propagates (D5).
                    raise ContentNotAvailableError(eli, "pdf") from exc
                _reject_if_too_large(eli, len(pdf_bytes), limit, pdf_url)
                markdown = await asyncio.to_thread(self._content_processor.pdf_to_text, pdf_bytes)
                if not markdown.strip():
                    raise ContentNotAvailableError(eli, "pdf")

            # Second gate, on the conversion *output*. The gates above bound the
            # input, which is enough for HTML — markdownify strips markup, so in
            # practice the result is smaller than the source (it can expand on
            # text dense in `_` and `*`, which it escapes, but a legal act in
            # HTML is the opposite of that). PDF runs the other way: text
            # streams are Flate-compressed, so a payload under the limit can
            # extract past it. Such a document would reach `DocumentStore.load`,
            # which truncates with nothing but a log line — the silent mid-clause
            # loss D6 rejected. One limit governs the whole path (D7).
            _reject_if_too_large(eli, len(markdown.encode("utf-8")), limit, pdf_url)
            sections = await asyncio.to_thread(self._content_processor.index_sections, markdown)
            await self._doc_store.load(eli, markdown, sections)
            logger.info(f"Loaded content for {eli}: {len(sections)} sections")
        except ResponseTooLargeError as exc:
            # The client knows the URL and the budget, not the act; the refusal the
            # agent reads has to name the act and the source file it can fetch instead.
            raise ContentTooLargeError(eli, exc.size_bytes, limit, pdf_url, exact=exc.exact) from exc

    def _format_toc(self, toc_data: list | dict) -> list[dict[str, Any]]:
        """Format TOC data for output."""
        if isinstance(toc_data, dict):
            toc_data = [toc_data]

        result = []
        for item in toc_data:
            if isinstance(item, dict):
                node = {
                    "id": item.get("id", ""),
                    "title": item.get("title", ""),
                    "type": item.get("type", ""),
                }
                children = item.get("children", [])
                if children:
                    node["children"] = self._format_toc(children)
                result.append(node)
        return result
