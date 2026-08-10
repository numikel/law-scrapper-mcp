"""Date calculation utility for legal date operations."""

import logging
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.models.tool_outputs import DateOutput, EnrichedResponse
from law_scrapper_mcp.services.response_enrichment import date_hints
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def register(mcp: MCPServer[AppContext]) -> None:
    """Register date calculation tool."""

    @mcp.tool(meta={"tags": ["dates", "utility"]})
    @handle_tool_errors
    async def calculate_legal_date(
        ctx: Context[AppContext],
        days: Annotated[
            str | int | bool,
            Field(
                description=(
                    "Liczba dni do dodania (+) lub odjęcia (-). Np. days=14 = za 14 dni, days=-14 = 14 dni temu. "
                    "Domyślnie 0. Wartości logiczne (true/false) są niedozwolone."
                ),
            ),
        ] = 0,
        months: Annotated[
            str | int | bool,
            Field(
                description=(
                    "Liczba miesięcy do dodania (+) lub odjęcia (-). "
                    "Np. months=3 = za 3 miesiące, months=-6 = 6 miesięcy temu. Domyślnie 0. "
                    "Wartości logiczne (true/false) są niedozwolone."
                ),
            ),
        ] = 0,
        years: Annotated[
            str | int | bool,
            Field(
                description=(
                    "Liczba lat do dodania (+) lub odjęcia (-). Np. years=1 = za rok, years=-5 = 5 lat temu. "
                    "Domyślnie 0. Wartości logiczne (true/false) są niedozwolone."
                ),
            ),
        ] = 0,
        base_date: Annotated[
            str | None,
            Field(
                description=(
                    "Data bazowa. Akceptowane formaty: 'YYYY-MM-DD' (np. '2024-01-15'), "
                    "'YYYY-MM' (np. '2024-01' → 2024-01-01), 'YYYY' (np. '2024' → 2024-01-01). "
                    "Jeśli nie podano, używa dzisiejszej daty."
                ),
            ),
        ] = None,
    ) -> EnrichedResponse[DateOutput]:
        """
        Oblicz daty na potrzeby prawne (np. terminy, daty wejścia w życie, vacatio legis).

        Dodaje lub odejmuje dni, miesiące i lata od daty bazowej (lub dzisiejszej).
        Wartości dodatnie = przyszłość, ujemne = przeszłość.

        Przykłady:
        - calculate_legal_date(days=14) - Termin 14 dni od dziś (np. termin na odwołanie)
        - calculate_legal_date(days=30, base_date="2024-06-01") - 30 dni od 1 czerwca 2024
        - calculate_legal_date(years=1, base_date="2024-01-01") - Rok po 1 stycznia 2024
        - calculate_legal_date(days=-90) - 90 dni wstecz od dziś
        - calculate_legal_date(months=6, days=15) - 6 miesięcy i 15 dni od dziś
        - calculate_legal_date(years=-5, base_date="2024") - 5 lat przed 1 stycznia 2024
        """
        output = get_app_context(ctx).date_service.calculate(days, months, years, base_date)
        return EnrichedResponse[DateOutput](data=output, hints=date_hints())
