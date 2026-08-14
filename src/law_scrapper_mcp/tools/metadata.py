"""System metadata tool for retrieving keywords, publishers, statuses, types, institutions."""

import logging
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.models.enums import MetadataCategory
from law_scrapper_mcp.models.tool_outputs import EnrichedResponse, MetadataOutput
from law_scrapper_mcp.services.response_enrichment import metadata_hints
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def register(mcp: MCPServer[AppContext]) -> None:
    """Register metadata tool."""

    @mcp.tool(meta={"tags": ["metadata"]})
    @handle_tool_errors
    async def get_system_metadata(
        ctx: Context[AppContext],
        category: Annotated[
            str,
            Field(
                description=(
                    "Kategoria metadanych: 'keywords' (słowa kluczowe do wyszukiwania), "
                    "'publishers' (wydawcy: DU, MP), 'statuses' (statusy aktów), "
                    "'types' (typy dokumentów: Ustawa, Rozporządzenie itp.), "
                    "'institutions' (instytucje wydające), 'all' (wszystkie kategorie). "
                    "Domyślnie 'all'."
                ),
            ),
        ] = "all",
        limit: Annotated[
            str | int | None,
            Field(
                description="Maksymalna liczba wartości metadanych na stronie odpowiedzi (domyślnie 20, maks. 100).",
            ),
        ] = 20,
        offset: Annotated[
            str | int | None,
            Field(description="Nieujemne przesunięcie strony metadanych."),
        ] = 0,
    ) -> EnrichedResponse[MetadataOutput]:
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
        metadata_service = get_app_context(ctx).metadata_service

        try:
            category_enum = MetadataCategory(category)
        except ValueError:
            category_enum = MetadataCategory.ALL

        output = await metadata_service.get_metadata_page(
            category_enum,
            limit=limit,
            offset=offset,
        )

        return EnrichedResponse[MetadataOutput](
            data=output,
            hints=metadata_hints(category_enum.value),
        )
