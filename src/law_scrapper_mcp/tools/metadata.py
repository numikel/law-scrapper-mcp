"""System metadata tool for retrieving keywords, publishers, statuses, types, institutions."""

import logging
from typing import Annotated

from fastmcp import Context, FastMCP

from law_scrapper_mcp.models.enums import MetadataCategory
from law_scrapper_mcp.models.tool_outputs import EnrichedResponse, MetadataOutput
from law_scrapper_mcp.services.response_enrichment import metadata_hints
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    """Register metadata tool."""

    @mcp.tool(tags={"metadata"})
    @handle_tool_errors(
        default_factory=lambda e, kw: MetadataOutput(
            category=kw.get("category", "all"),
            metadata={},
            count=0,
        ),
    )
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
        metadata_service = ctx.request_context.lifespan_context["metadata_service"]

        # Convert string to enum
        try:
            category_enum = MetadataCategory(category)
        except ValueError:
            category_enum = MetadataCategory.ALL

        metadata = await metadata_service.get_metadata(category_enum)

        response = EnrichedResponse(
            data=MetadataOutput(
                category=category,
                metadata=metadata,
                count=sum(
                    len(v) if isinstance(v, list) else 1 for v in metadata.values()
                ),
            ),
            hints=metadata_hints(category),
        )

        return response.model_dump_json()
