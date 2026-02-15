"""Browse legal acts by publisher and year."""

import contextlib
import logging
from typing import Annotated

from fastmcp import Context, FastMCP

from law_scrapper_mcp.models.enums import DetailLevel
from law_scrapper_mcp.models.tool_outputs import EnrichedResponse, SearchOutput
from law_scrapper_mcp.services.response_enrichment import search_hints
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)

DEFAULT_BROWSE_LIMIT = 20


def register(mcp: FastMCP) -> None:
    """Register browse tool."""

    @mcp.tool(tags={"search", "browse"})
    @handle_tool_errors(
        default_factory=lambda e, kw: SearchOutput(
            results=[],
            total_count=0,
            query_summary=f"publisher={kw.get('publisher', '')} | year={kw.get('year', '')}",
            returned_count=0,
        ),
    )
    async def browse_acts(
        publisher: Annotated[
            str,
            "Kod wydawcy: 'DU' (Dziennik Ustaw) lub 'MP' (Monitor Polski).",
        ],
        year: Annotated[str | int, "Rok publikacji (np. 2024)."],
        limit: Annotated[
            str | int | None,
            "Maksymalna liczba wyników do zwrócenia. Domyślnie 20.",
        ] = None,
        detail_level: Annotated[
            str,
            "Poziom szczegółowości: 'minimal' (ELI, tytuł, status), "
            "'standard' (+ typ, daty, obowiązywanie), 'full' (wszystkie pola). Domyślnie 'standard'.",
        ] = "standard",
        ctx: Context = None,
    ) -> str:
        """
        Przeglądaj wszystkie akty prawne wydane przez wydawcę w danym roku.

        Kiedy użyć: Gdy chcesz przeglądać cały rocznik wydawcy bez filtrowania.
        Kiedy NIE używać: Gdy szukasz po słowach kluczowych lub tytule → użyj search_legal_acts.

        Zwraca listę aktów (domyślnie max 20). Wyniki zapisywane są do result_set_id
        — użyj filter_results aby je zawęzić.

        Przykłady:
        - browse_acts(publisher="DU", year=2024) - Wszystkie akty DU z 2024
        - browse_acts(publisher="MP", year=2024) - Wszystkie akty MP z 2024
        - browse_acts(publisher="DU", year=2024, detail_level="full") - Ze szczegółami
        - browse_acts(publisher="DU", year=2024, detail_level="minimal") - Tylko podstawowe info
        - browse_acts(publisher="DU", year=2000) - Akty z roku 2000
        """
        assert ctx is not None
        search_service = ctx.request_context.lifespan_context["search_service"]
        result_store = ctx.request_context.lifespan_context["result_store"]

        # Normalize year (MCP clients may send string)
        year_int = 0
        with contextlib.suppress(ValueError, TypeError):
            year_int = int(year)

        limit_int: int | None = None
        if limit is not None:
            with contextlib.suppress(ValueError, TypeError):
                limit_int = int(limit)

        # Convert detail_level string to enum
        try:
            detail_enum = DetailLevel(detail_level)
        except ValueError:
            detail_enum = DetailLevel.STANDARD

        results, total_count = await search_service.browse(
            publisher=publisher,
            year=year_int,
            detail_level=detail_enum,
        )

        # Apply default limit if no explicit limit was provided
        effective_limit = limit_int if limit_int is not None else DEFAULT_BROWSE_LIMIT
        was_truncated = len(results) > effective_limit
        if was_truncated:
            results = results[:effective_limit]

        # Store results for subsequent filtering
        query_summary = f"publisher={publisher} | year={year}"
        result_set_id = None
        if results:
            result_set_id = await result_store.store(results, query_summary, total_count)

        first_eli = results[0].eli if results else None

        response = EnrichedResponse(
            data=SearchOutput(
                results=results,
                total_count=total_count,
                query_summary=query_summary,
                returned_count=len(results),
                result_set_id=result_set_id,
            ),
            hints=search_hints(
                total_count,
                len(results) > 0,
                first_eli,
                result_set_id,
                was_truncated=was_truncated,
                applied_limit=effective_limit,
            ),
        )

        return response.model_dump_json()
