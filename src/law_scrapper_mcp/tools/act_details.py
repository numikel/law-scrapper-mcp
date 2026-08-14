"""Get detailed information about a specific legal act."""

import logging
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.models.tool_outputs import ActDetailOutput, EnrichedResponse
from law_scrapper_mcp.services.response_enrichment import act_details_hints
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def register(mcp: MCPServer[AppContext]) -> None:
    """Register act details tool."""

    @mcp.tool(meta={"tags": ["analysis", "details"]})
    @handle_tool_errors
    async def get_act_details(
        eli: Annotated[
            str,
            Field(
                description=(
                    'Identyfikator ELI aktu. Format: "{wydawca}/{rok}/{pozycja}". '
                    "Wydawcy: DU (Dziennik Ustaw), MP (Monitor Polski). "
                    'Przykłady: "DU/2024/1716", "MP/2023/500", "DU/2024/1".'
                ),
            ),
        ],
        ctx: Context[AppContext],
        load_content: Annotated[
            str | bool,
            Field(
                description=(
                    "Załaduj treść aktu do Document Store (pamięć). "
                    "WYMAGANE przed użyciem: read_act_content, search_in_act. "
                    "Lifecycle: załadowane → TTL 2h → wygasa → wymaga ponownego załadowania. "
                    "Domyślnie False."
                ),
            ),
        ] = False,
    ) -> EnrichedResponse[ActDetailOutput]:
        """
        Pobierz szczegółowe informacje o akcie prawnym.

        Zwraca metadane: tytuł, status, daty (ogłoszenia, wejścia w życie, uchylenia),
        słowa kluczowe, spis treści i dostępne formaty (HTML/PDF).

        Ustaw load_content=True aby załadować treść — to wymagane przed użyciem
        read_act_content (czytanie sekcji) i search_in_act (wyszukiwanie w treści).

        Kiedy użyć: Aby zobaczyć metadane aktu (status, daty, spis treści).
        Kiedy NIE używać: Aby czytać treść aktu → użyj read_act_content (po załadowaniu).

        Przykłady:
        - get_act_details(eli="DU/2024/1692") - Metadane i spis treści
        - get_act_details(eli="DU/2024/1692", load_content=True) - Załaduj treść do analizy
        - get_act_details(eli="DU/2024/1716") - Sprawdź status, daty i referencje
        - get_act_details(eli="MP/2024/100") - Akt z Monitora Polskiego
        - get_act_details(eli="DU/2021/1500") - Sprawdź status i daty obowiązywania
        """
        act_service = get_app_context(ctx).act_service

        if isinstance(load_content, str):
            load_content_bool = load_content.lower() in ("true", "1", "yes")
        else:
            load_content_bool = bool(load_content)

        act_details = await act_service.get_details(eli=eli, load_content=load_content_bool)

        return EnrichedResponse[ActDetailOutput](
            data=act_details,
            hints=act_details_hints(
                eli,
                act_details.is_loaded,
                act_details.has_html,
                just_loaded=load_content_bool and act_details.is_loaded,
            ),
        )
