"""Get detailed information about a specific legal act."""

import logging
from typing import Annotated

from fastmcp import Context, FastMCP

from law_scrapper_mcp.models.tool_outputs import ActDetailOutput, EnrichedResponse
from law_scrapper_mcp.services.response_enrichment import act_details_hints
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    """Register act details tool."""

    @mcp.tool(tags={"analysis", "details"})
    @handle_tool_errors(
        default_factory=lambda e, kw: ActDetailOutput(
            eli=kw.get("eli", ""),
            publisher="",
            year=0,
            pos=0,
            title="",
            status="",
            has_pdf=False,
            has_html=False,
            toc=[],
            is_loaded=False,
        ),
    )
    async def get_act_details(
        eli: Annotated[
            str,
            "Identyfikator ELI aktu. Format: \"{wydawca}/{rok}/{pozycja}\". "
            "Wydawcy: DU (Dziennik Ustaw), MP (Monitor Polski). "
            "Przykłady: \"DU/2024/1716\", \"MP/2023/500\", \"DU/2024/1\".",
        ],
        load_content: Annotated[
            str | bool,
            "Załaduj treść aktu do Document Store (pamięć). "
            "WYMAGANE przed użyciem: read_act_content, search_in_act. "
            "Lifecycle: załadowane → TTL 2h → wygasa → wymaga ponownego załadowania. "
            "Domyślnie False.",
        ] = False,
        ctx: Context = None,
    ) -> str:
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
        assert ctx is not None
        act_service = ctx.request_context.lifespan_context["act_service"]

        # Normalize bool (MCP clients may send string)
        if isinstance(load_content, str):
            load_content_bool = load_content.lower() in ("true", "1", "yes")
        else:
            load_content_bool = bool(load_content)

        act_details = await act_service.get_details(eli=eli, load_content=load_content_bool)

        response = EnrichedResponse(
            data=act_details,
            hints=act_details_hints(
                eli,
                act_details.is_loaded,
                act_details.has_html,
                just_loaded=load_content_bool and act_details.is_loaded,
            ),
        )

        return response.model_dump_json()
