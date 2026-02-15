"""Search within loaded legal acts."""

import contextlib
import logging
from typing import Annotated

from fastmcp import Context, FastMCP

from law_scrapper_mcp.models.tool_outputs import EnrichedResponse, SearchInActOutput
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    """Register search in act tool."""

    @mcp.tool(tags={"analysis", "search"})
    @handle_tool_errors(
        default_factory=lambda e, kw: SearchInActOutput(
            eli=kw.get("eli", ""),
            query=kw.get("query", ""),
            matches=[],
            total_matches=0,
        ),
    )
    async def search_in_act(
        eli: Annotated[
            str,
            "Identyfikator ELI aktu. Format: \"{wydawca}/{rok}/{pozycja}\". "
            "Wydawcy: DU (Dziennik Ustaw), MP (Monitor Polski). "
            "Przykłady: \"DU/2024/1716\", \"MP/2023/500\", \"DU/2024/1\". "
            "Akt MUSI być wcześniej załadowany przez get_act_details(eli=..., load_content=True).",
        ],
        query: Annotated[
            str,
            "Termin do wyszukania w treści aktu (np. 'podatek', 'obowiązek', 'art. 5'). "
            "Wielkość liter jest ignorowana.",
        ],
        context_chars: Annotated[
            str | int,
            "Liczba znaków kontekstu przed i po każdym trafieniu. Domyślnie 500.",
        ] = 500,
        ctx: Context = None,
    ) -> str:
        """
        Wyszukaj termin w treści załadowanego aktu prawnego.

        WYMAGANIE: Akt musi być wcześniej załadowany za pomocą
        get_act_details(eli=..., load_content=True).

        Zwraca wszystkie trafienia z kontekstem i informacją o sekcji.

        Przykłady:
        - search_in_act(eli="DU/2024/1692", query="straż") - Znajdź "straż" w akcie
        - search_in_act(eli="DU/2024/1692", query="obowiązek", context_chars=200) - Z krótszym kontekstem
        - search_in_act(eli="DU/2024/1692", query="art. 5") - Odwołania do artykułu 5
        - search_in_act(eli="DU/2024/1692", query="kara") - Fragmenty o karach
        - search_in_act(eli="DU/2024/1692", query="termin") - Wszystkie wzmianki o terminach
        """
        assert ctx is not None
        document_store = ctx.request_context.lifespan_context["document_store"]

        # Normalize int (MCP clients may send string)
        context_chars_int = 500
        with contextlib.suppress(ValueError, TypeError):
            context_chars_int = int(context_chars)

        hits = await document_store.search(eli, query, context_chars_int)

        matches = [
            {
                "section_id": hit.section_id,
                "section_title": hit.section_title,
                "context": hit.context,
                "position": f"{hit.match_start}-{hit.match_end}",
            }
            for hit in hits
        ]

        response = EnrichedResponse(
            data=SearchInActOutput(
                eli=eli,
                query=query,
                matches=matches,
                total_matches=len(matches),
            ),
        )

        return response.model_dump_json()
