"""Act details service with content loading."""

import asyncio
import logging
from typing import Any

from law_scrapper_mcp.client.exceptions import ContentTooLargeError, ResponseTooLargeError
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.config import settings
from law_scrapper_mcp.models.tool_inputs import parse_eli
from law_scrapper_mcp.models.tool_outputs import ActDetailOutput
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

        # Load content if requested
        is_loaded = await self._doc_store.is_loaded(eli)
        if load_content and not is_loaded:
            await self._load_content(eli, publisher, year, pos, has_html)
            is_loaded = await self._doc_store.is_loaded(eli)

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
        )

    async def _load_content(self, eli: str, publisher: str, year: int, pos: int, has_html: bool) -> None:
        """Load act content into document store."""
        pdf_url = f"{self._client.BASE_URL}/acts/{publisher}/{year}/{pos}/text.pdf"
        limit = settings.doc_store_max_size_bytes
        try:
            # The same limit reaches the download itself (#19): the client aborts a
            # body that runs past it while it is still streaming, so an oversized act
            # never sits whole in memory. The post-hoc gates below stay as belt and
            # braces — they bound what the converter sees, which streaming does not.
            if has_html:
                html = await self._client.get_act_html(publisher, year, pos, max_bytes=limit)
                _reject_if_too_large(eli, len(html.encode("utf-8")), limit, pdf_url)
                # markdownify, pdfplumber and the section regex are synchronous
                # CPU-bound work. Left in the coroutine they hold the event loop
                # for seconds, `/health` included. The offload stops at
                # `ContentProcessor`: `DocumentStore` relies on the absence of
                # `await` in its critical sections (see its class docstring).
                markdown = await asyncio.to_thread(self._content_processor.html_to_markdown, html)
            else:
                # try/except/else, not a single try block: the fallback below is
                # for an unreachable PDF, and it must not swallow a size refusal —
                # neither one raised mid-download nor one raised after it succeeded.
                try:
                    pdf_bytes = await self._client.get_bytes(f"acts/{publisher}/{year}/{pos}/text.pdf", max_bytes=limit)
                except ResponseTooLargeError:
                    raise
                except Exception:
                    markdown = f"*No readable content available for {eli}. PDF URL: {pdf_url}*"
                else:
                    _reject_if_too_large(eli, len(pdf_bytes), limit, pdf_url)
                    markdown = await asyncio.to_thread(self._content_processor.pdf_to_text, pdf_bytes)
                    if not markdown:
                        markdown = f"*Content extraction failed. PDF available at: {pdf_url}*"

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
        except ContentTooLargeError:
            # The only failure the agent can act on, so it is the only one that
            # reaches the tool layer instead of being logged and hidden.
            raise
        except Exception as e:
            logger.error(f"Failed to load content for {eli}: {e}")

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
