"""Track legal changes within date ranges."""

import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP

from law_scrapper_mcp.context import get_app_context
from law_scrapper_mcp.models.pagination import empty_item_page_info
from law_scrapper_mcp.models.tool_outputs import ChangesOutput, EnrichedResponse
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def _changes_error_output(_: Exception, kw: dict[str, Any]) -> ChangesOutput:
    return ChangesOutput(
        date_range=f"{kw.get('date_from', '')} do {kw.get('date_to', 'dziś')}",
        publisher=kw.get("publisher", "DU"),
        keywords=kw.get("keywords") or [],
        changes=[],
        total_count=0,
        page_info=empty_item_page_info(),
    )


def register(mcp: FastMCP) -> None:
    """Register changes tracking tool."""

    @mcp.tool(tags={"analysis", "tracking"})
    @handle_tool_errors(default_factory=_changes_error_output)
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
        limit: Annotated[
            str | int | None,
            "Maksymalna liczba zmian na stronie odpowiedzi (domyślnie 20, maks. 100).",
        ] = 20,
        offset: Annotated[
            str | int | None,
            "Nieujemne przesunięcie strony zmian.",
        ] = 0,
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
        changes_service = get_app_context(ctx).changes_service

        output = await changes_service.track_changes(
            publisher=publisher,
            date_from=date_from,
            date_to=date_to,
            keywords=keywords,
            limit=int(limit) if limit is not None else 20,
            offset=int(offset) if offset is not None else 0,
        )

        response = EnrichedResponse(data=output)

        return response.model_dump_json()
