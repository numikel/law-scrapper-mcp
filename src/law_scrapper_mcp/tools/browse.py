"""Browse legal acts by publisher and year."""

import logging
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from law_scrapper_mcp.config import settings
from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.models.enums import DetailLevel
from law_scrapper_mcp.models.tool_outputs import EnrichedResponse, SearchOutput
from law_scrapper_mcp.services.pagination import (
    DEFAULT_ITEM_LIMIT,
    MAX_ITEM_LIMIT,
    parse_non_negative,
)
from law_scrapper_mcp.services.pagination import effective_limit as clamp_limit
from law_scrapper_mcp.services.response_enrichment import search_hints
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)

DEFAULT_BROWSE_LIMIT = 20


def register(mcp: MCPServer[AppContext]) -> None:
    """Register browse tool."""

    @mcp.tool(meta={"tags": ["search", "browse"]})
    @handle_tool_errors
    async def browse_acts(
        publisher: Annotated[
            str,
            Field(description="Kod wydawcy: 'DU' (Dziennik Ustaw) lub 'MP' (Monitor Polski)."),
        ],
        year: Annotated[
            str | int,
            Field(description="Rok publikacji (np. 2024)."),
        ],
        ctx: Context[AppContext],
        limit: Annotated[
            str | int | None,
            Field(
                description=(
                    "Maksymalna liczba wyników do zwrócenia. Domyślnie 20, maksymalnie 100 — "
                    "wartości powyżej są przycinane, tak samo jak w pozostałych narzędziach listujących."
                ),
            ),
        ] = None,
        offset: Annotated[
            str | int | None,
            Field(description="Liczba wyników do pominięcia (paginacja). Użyj razem z limit. Domyślnie 0."),
        ] = None,
        detail_level: Annotated[
            str,
            Field(
                description=(
                    "Poziom szczegółowości: 'minimal' (ELI, tytuł, status), "
                    "'standard' (+ typ, daty, obowiązywanie), 'full' (wszystkie pola). Domyślnie 'standard'."
                ),
            ),
        ] = "standard",
    ) -> EnrichedResponse[SearchOutput]:
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
        - browse_acts(publisher="DU", year=2024, limit=20, offset=20) - Druga strona rocznika
        """
        search_service = get_app_context(ctx).search_service

        try:
            year_int = int(year)
        except (ValueError, TypeError) as e:
            raise ValueError("Rok musi być liczbą całkowitą.") from e

        # Clamped, and no longer silently: since `browse()` started paging through
        # `acts/search`, `limit` reaches the wire and decides how wide a page the Sejm
        # API builds. The year endpoint used to ignore it, which is why this tool was
        # the one list tool never routed through `effective_limit` — the endpoint
        # changed underneath it and the guard was never added. An unbounded value here
        # asks a public API for as many records as an agent cares to type.
        limit_int: int | None = None
        if limit is not None:
            limit_int = clamp_limit(limit, default=DEFAULT_ITEM_LIMIT, maximum=MAX_ITEM_LIMIT)

        offset_int: int | None = None
        if offset is not None:
            offset_int = parse_non_negative(offset, name="offset", default=0)

        try:
            detail_enum = DetailLevel(detail_level)
        except ValueError:
            detail_enum = DetailLevel.STANDARD

        output = await search_service.browse(
            publisher=publisher,
            year=year_int,
            detail_level=detail_enum,
            limit=limit_int,
            offset=offset_int,
        )

        applied_limit = limit_int if limit_int is not None else DEFAULT_BROWSE_LIMIT
        first_eli = output.results[0].eli if output.results else None

        next_call_params: dict[str, Any] = {"publisher": publisher, "year": year_int}
        if detail_enum is not DetailLevel.STANDARD:
            next_call_params["detail_level"] = detail_enum.value

        return EnrichedResponse[SearchOutput](
            data=output,
            hints=search_hints(
                output.total_count,
                output.returned_count > 0,
                first_eli,
                output.result_set_id,
                tool_name="browse_acts",
                next_call_params=next_call_params,
                filter_max_records=settings.effective_filter_max_records,
                scope=output.result_set_scope,
                offset=offset_int or 0,
                returned_count=output.returned_count,
                applied_limit=applied_limit,
            ),
        )
