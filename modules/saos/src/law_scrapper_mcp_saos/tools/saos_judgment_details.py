"""`get_saos_judgment_details`: the court portal link and division type of one SAOS judgment."""

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.models.tool_outputs import EnrichedResponse
from law_scrapper_mcp.tools.annotations import READ_ONLY_REMOTE
from law_scrapper_mcp.tools.error_handling import handle_tool_errors
from law_scrapper_mcp_saos.models import SaosJudgmentDetailsOutput
from law_scrapper_mcp_saos.service import get_saos_judgment_details as run_details


def register(mcp: MCPServer[AppContext]) -> None:
    """Register the judgment details tool."""

    @mcp.tool(
        title="Szczegóły orzeczenia z SAOS",
        annotations=READ_ONLY_REMOTE,
        meta={"tags": ["judgments", "saos"]},
    )
    @handle_tool_errors
    async def get_saos_judgment_details(
        ctx: Context[AppContext],
        source_id: Annotated[
            str | int,
            Field(
                description=(
                    "Identyfikator orzeczenia w SAOS: liczba z pola source_id wyniku search_saos_judgments "
                    "(np. 554697)."
                ),
            ),
        ],
    ) -> EnrichedResponse[SaosJudgmentDetailsOutput]:
        """
        Pobierz z SAOS szczegóły jednego orzeczenia: odnośnik do orzeczenia w portalu sądu i rodzaj wydziału.

        Zwraca rekord orzeczenia (sygnatura, sąd, wydział, rodzaj, data, odnośnik w SAOS) uzupełniony o
        portal_url, czyli adres orzeczenia podany przez sąd, oraz o division_type. Wykonuje jedno żądanie do
        SAOS. Nie zwraca treści orzeczenia, sędziów ani innych danych osobowych. Adres portalu pochodzi od
        sądu: bywa adresem API jego systemu zamiast strony dla człowieka, a serwer go nie pobiera.

        Kiedy użyć: Masz source_id z wyniku wyszukiwania i potrzebujesz odnośnika do portalu sądu.
        Kiedy użyć: Wyszukiwanie z include_portal_links objęło tylko pierwsze wyniki, a potrzebujesz kolejnych.
        Kiedy NIE używać: Szukasz orzeczeń → użyj search_saos_judgments.
        Kiedy NIE używać: Potrzebujesz treści uzasadnienia — to narzędzie jej nie zwraca.

        Przykłady:
        - get_saos_judgment_details(source_id="554697") - Odnośnik do wyroku z wydziału pracy
        - get_saos_judgment_details(source_id=554698) - Identyfikator może być liczbą
        - get_saos_judgment_details(source_id="245360") - Orzeczenie Sądu Najwyższego (zwykle bez rodzaju wydziału)
        - get_saos_judgment_details(source_id="12510") - Starsze orzeczenie, odnośnik może być adresem http
        - get_saos_judgment_details(source_id=" 554697 ") - Spacje wokół liczby są ignorowane
        """
        return await run_details(get_app_context(ctx), source_id=source_id)
