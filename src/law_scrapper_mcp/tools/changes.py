"""Track legal changes within date ranges."""

import logging
from typing import Annotated

from fastmcp import Context, FastMCP

from law_scrapper_mcp.models.tool_outputs import ChangesOutput, EnrichedResponse
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    """Register changes tracking tool."""

    @mcp.tool(tags={"analysis", "tracking"})
    @handle_tool_errors(
        default_factory=lambda e, kw: ChangesOutput(
            date_range=f"{kw.get('date_from', '')} do {kw.get('date_to', 'dziś')}",
            publisher=kw.get("publisher", "DU"),
            keywords=kw.get("keywords") or [],
            changes=[],
            total_count=0,
        ),
    )
    async def track_legal_changes(
        date_from: Annotated[
            str,
            "Data początkowa śledzenia (YYYY-MM-DD). Np. '2024-01-01'.",
        ],
        publisher: Annotated[
            str,
            "Kod wydawcy: 'DU' (Dziennik Ustaw) lub 'MP' (Monitor Polski). Domyślnie 'DU'.",
        ] = "DU",
        date_to: Annotated[
            str | None,
            "Data końcowa śledzenia (YYYY-MM-DD). Domyślnie dzisiejsza data.",
        ] = None,
        keywords: Annotated[
            list[str] | None,
            "Słowa kluczowe do filtrowania zmian (logika AND). Np. ['podatek'], ['zdrowotny', 'ubezpieczenie'].",
        ] = None,
        ctx: Context = None,
    ) -> str:
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
        assert ctx is not None
        changes_service = ctx.lifespan_context.changes_service

        output = await changes_service.track_changes(
            publisher=publisher,
            date_from=date_from,
            date_to=date_to,
            keywords=keywords,
        )

        response = EnrichedResponse(data=output)

        return response.model_dump_json()
