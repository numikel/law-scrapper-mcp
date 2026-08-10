"""Track legal changes within date ranges."""

import logging
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.models.tool_outputs import ChangesOutput, EnrichedResponse
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def register(mcp: MCPServer[AppContext]) -> None:
    """Register changes tracking tool."""

    @mcp.tool(meta={"tags": ["analysis", "tracking"]})
    @handle_tool_errors
    async def track_legal_changes(
        date_from: Annotated[
            str,
            Field(description="Data początkowa śledzenia (YYYY-MM-DD). Np. '2024-01-01'."),
        ],
        ctx: Context[AppContext],
        publisher: Annotated[
            str,
            Field(description="Kod wydawcy: 'DU' (Dziennik Ustaw) lub 'MP' (Monitor Polski). Domyślnie 'DU'."),
        ] = "DU",
        date_to: Annotated[
            str | None,
            Field(description="Data końcowa śledzenia (YYYY-MM-DD). Domyślnie dzisiejsza data."),
        ] = None,
        keywords: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Słowa kluczowe do filtrowania zmian (logika AND). Np. ['podatek'], ['zdrowotny', 'ubezpieczenie']."
                ),
            ),
        ] = None,
        limit: Annotated[
            str | int | None,
            Field(description="Maksymalna liczba zmian na stronie odpowiedzi (domyślnie 20, maks. 100)."),
        ] = 20,
        offset: Annotated[
            str | int | None,
            Field(description="Nieujemne przesunięcie strony zmian."),
        ] = 0,
    ) -> EnrichedResponse[ChangesOutput]:
        """
        Śledź zmiany prawne i nowe publikacje w zakresie dat.

        Zwraca akty prawne opublikowane lub zmienione w podanym zakresie dat.
        Wyniki zapisywane są do result_set_id — użyj filter_results aby je zawęzić
        (np. po typie dokumentu lub wzorcem regex w tytule).

        Przykłady:
        - track_legal_changes(date_from="2025-01-01") - Zmiany od początku 2025
        - track_legal_changes(date_from="2024-01-01", date_to="2024-12-31") - Zmiany w 2024
        - track_legal_changes(date_from="2024-01-01", keywords=["podatek"]) - Zmiany podatkowe w 2024
        - track_legal_changes(date_from="2024-06-01", publisher="MP") - Zmiany w MP od czerwca 2024
        - track_legal_changes(date_from="2024-01-01", keywords=["zdrowotny"]) - Zmiany zdrowotne
        """
        changes_service = get_app_context(ctx).changes_service

        output = await changes_service.track_changes(
            publisher=publisher,
            date_from=date_from,
            date_to=date_to,
            keywords=keywords,
            limit=int(limit) if limit is not None else 20,
            offset=int(offset) if offset is not None else 0,
        )

        return EnrichedResponse[ChangesOutput](data=output)
