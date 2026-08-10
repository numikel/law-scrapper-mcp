"""Search within loaded legal acts."""

import logging
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.models.pagination import (
    DEFAULT_ITEM_LIMIT,
    MAX_CONTEXT_CHARS,
    MAX_ITEM_LIMIT,
)
from law_scrapper_mcp.models.tool_outputs import EnrichedResponse, SearchInActOutput
from law_scrapper_mcp.services.pagination import (
    effective_limit,
    paginate_items,
    parse_non_negative,
)
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def register(mcp: MCPServer[AppContext]) -> None:
    """Register search in act tool."""

    @mcp.tool(meta={"tags": ["analysis", "search"]})
    @handle_tool_errors
    async def search_in_act(
        eli: Annotated[
            str,
            Field(
                description=(
                    'Identyfikator ELI aktu. Format: "{wydawca}/{rok}/{pozycja}". '
                    "Wydawcy: DU (Dziennik Ustaw), MP (Monitor Polski). "
                    'Przykłady: "DU/2024/1716", "MP/2023/500", "DU/2024/1". '
                    "Akt MUSI być wcześniej załadowany przez get_act_details(eli=..., load_content=True)."
                ),
            ),
        ],
        query: Annotated[
            str,
            Field(
                description=(
                    "Termin do wyszukania w treści aktu (np. 'podatek', 'obowiązek', 'art. 5'). "
                    "Wielkość liter jest ignorowana."
                ),
            ),
        ],
        ctx: Context[AppContext],
        context_chars: Annotated[
            str | int,
            Field(description="Liczba znaków kontekstu przed i po każdym trafieniu. Domyślnie 500."),
        ] = 500,
        limit: Annotated[
            str | int,
            Field(description="Maksymalna liczba trafień na stronie. Domyślnie 20, maksimum 100."),
        ] = 20,
        offset: Annotated[
            str | int,
            Field(description="Nieujemne przesunięcie początku strony. Domyślnie 0."),
        ] = 0,
    ) -> EnrichedResponse[SearchInActOutput]:
        """
        Wyszukaj termin w treści załadowanego aktu prawnego.

        WYMAGANIE: Akt musi być wcześniej załadowany za pomocą
        get_act_details(eli=..., load_content=True).

        Zwraca jedną stronę trafień z kontekstem, metadanymi paginacji i informacją o sekcji.

        Przykłady:
        - search_in_act(eli="DU/2024/1692", query="straż") - Znajdź "straż" w akcie
        - search_in_act(eli="DU/2024/1692", query="obowiązek", context_chars=200) - Z krótszym kontekstem
        - search_in_act(eli="DU/2024/1692", query="art. 5", limit=10, offset=0) - Pierwsza strona trafień
        - search_in_act(eli="DU/2024/1692", query="kara", limit=5, offset=5) - Kolejna strona trafień
        - search_in_act(eli="DU/2024/1692", query="termin") - Domyślna strona (do 20 trafień)
        """
        document_store = get_app_context(ctx).document_store

        context_size = min(
            parse_non_negative(context_chars, name="context_chars", default=500),
            MAX_CONTEXT_CHARS,
        )
        page_limit = effective_limit(limit, default=DEFAULT_ITEM_LIMIT, maximum=MAX_ITEM_LIMIT)
        page_offset = parse_non_negative(offset, name="offset", default=0)
        hits = await document_store.search(eli, query, context_size)
        all_matches = [
            {
                "section_id": hit.section_id,
                "section_title": hit.section_title,
                "context": hit.context,
                "position": f"{hit.match_start}-{hit.match_end}",
            }
            for hit in hits
        ]
        matches, page_info = paginate_items(all_matches, limit=page_limit, offset=page_offset)
        output = SearchInActOutput(
            eli=eli,
            query=query,
            matches=matches,
            total_matches=page_info.total_count,
            page_info=page_info,
        )

        return EnrichedResponse[SearchInActOutput](data=output)
