"""System metadata tool for retrieving keywords, publishers, statuses, types, institutions."""

import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP

from law_scrapper_mcp.context import get_app_context
from law_scrapper_mcp.models.enums import MetadataCategory
from law_scrapper_mcp.models.pagination import empty_item_page_info
from law_scrapper_mcp.models.tool_outputs import EnrichedResponse, MetadataOutput
from law_scrapper_mcp.services.pagination import full_item_page
from law_scrapper_mcp.services.response_enrichment import metadata_hints
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def _metadata_error_output(_: Exception, kw: dict[str, Any]) -> MetadataOutput:
    return MetadataOutput(
        category=kw.get("category", "all"),
        metadata={},
        count=0,
        page_info=empty_item_page_info(),
    )


def register(mcp: FastMCP) -> None:
    """Register metadata tool."""

    @mcp.tool(tags={"metadata"})
    @handle_tool_errors(default_factory=_metadata_error_output)
    async def get_system_metadata(
        category: Annotated[
            str,
            "Kategoria metadanych: 'keywords' (słowa kluczowe do wyszukiwania), "
            "'publishers' (wydawcy: DU, MP), 'statuses' (statusy aktów), "
            "'types' (typy dokumentów: Ustawa, Rozporządzenie itp.), "
            "'institutions' (instytucje wydające), 'all' (wszystkie kategorie). "
            "Domyślnie 'all'.",
        ] = "all",
        ctx: Context = None,
    ) -> str:
        """
        Pobierz metadane systemu aktów prawnych.

        Zwraca dostępne słowa kluczowe, wydawców, typy dokumentów, statusy i instytucje,
        które można wykorzystać do filtrowania i wyszukiwania aktów prawnych.

        Przykłady:
        - get_system_metadata(category="keywords") - Dostępne słowa kluczowe do wyszukiwania
        - get_system_metadata(category="types") - Typy dokumentów (Ustawa, Rozporządzenie itp.)
        - get_system_metadata(category="publishers") - Wydawcy (DU, MP)
        - get_system_metadata(category="statuses") - Statusy aktów (obowiązujący, uchylony itp.)
        - get_system_metadata(category="all") - Wszystkie kategorie metadanych
        """
        assert ctx is not None
        metadata_service = get_app_context(ctx).metadata_service

        try:
            category_enum = MetadataCategory(category)
        except ValueError:
            category_enum = MetadataCategory.ALL

        metadata = await metadata_service.get_metadata(category_enum)
        flattened = [
            item
            for values in metadata.values()
            for item in (values if isinstance(values, list) else [values])
        ]
        _, page_info = full_item_page(flattened)

        response = EnrichedResponse(
            data=MetadataOutput(
                category=category,
                metadata=metadata,
                count=page_info.returned_count,
                page_info=page_info,
            ),
            hints=metadata_hints(category),
        )

        return response.model_dump_json()
