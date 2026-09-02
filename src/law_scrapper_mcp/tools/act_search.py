"""Search within loaded legal acts."""

import logging
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.models.pagination import DEFAULT_CONTEXT_CHARS, MAX_CONTEXT_CHARS
from law_scrapper_mcp.models.tool_outputs import EnrichedResponse, SearchInActOutput
from law_scrapper_mcp.services.response_enrichment import search_in_act_hints
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
            Field(
                description=(
                    f"Liczba znaków kontekstu przed i po każdym trafieniu. Domyślnie {DEFAULT_CONTEXT_CHARS}. "
                    f"Maksymalnie {MAX_CONTEXT_CHARS} — wartości większe są przycinane do {MAX_CONTEXT_CHARS}, "
                    "a odpowiedź zawiera wtedy wskazówkę o przycięciu. Wywołanie nie kończy się błędem."
                ),
            ),
        ] = DEFAULT_CONTEXT_CHARS,
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
        content_service = get_app_context(ctx).content_service

        output = await content_service.search(
            eli,
            query,
            context_chars=context_chars,
            limit=limit,
            offset=offset,
        )

        return EnrichedResponse[SearchInActOutput](
            data=output,
            hints=search_in_act_hints(output.context_chars_requested, output.context_chars_applied),
        )
