"""Act details service with content loading."""

import logging
from typing import Any

from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.config import settings
from law_scrapper_mcp.models.tool_inputs import parse_eli
from law_scrapper_mcp.models.tool_outputs import ActDetailOutput
from law_scrapper_mcp.services.content_processor import ContentProcessor
from law_scrapper_mcp.services.document_store import DocumentStore

logger = logging.getLogger(__name__)


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
        data = await self._client.get_json(
            f"acts/{publisher}/{year}/{pos}", cache_ttl=settings.cache_details_ttl
        )

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

    async def _load_content(
        self, eli: str, publisher: str, year: int, pos: int, has_html: bool
    ) -> None:
        """Load act content into document store."""
        try:
            if has_html:
                html = await self._client.get_act_html(publisher, year, pos)
                markdown = self._content_processor.html_to_markdown(html)
            else:
                # Try PDF
                try:
                    pdf_bytes = await self._client.get_bytes(
                        f"acts/{publisher}/{year}/{pos}/text.pdf"
                    )
                    markdown = self._content_processor.pdf_to_text(pdf_bytes)
                    if not markdown:
                        markdown = f"*Content extraction failed. PDF available at: {self._client.BASE_URL}/acts/{publisher}/{year}/{pos}/text.pdf*"
                except Exception:
                    markdown = f"*No readable content available for {eli}. PDF URL: {self._client.BASE_URL}/acts/{publisher}/{year}/{pos}/text.pdf*"

            sections = self._content_processor.index_sections(markdown)
            await self._doc_store.load(eli, markdown, sections)
            logger.info(f"Loaded content for {eli}: {len(sections)} sections")
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
