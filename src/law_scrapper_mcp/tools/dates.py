"""Date calculation utility for legal date operations."""

import contextlib
import logging
import re
from datetime import datetime
from typing import Annotated

from fastmcp import Context, FastMCP

from law_scrapper_mcp.models.tool_outputs import DateOutput, EnrichedResponse
from law_scrapper_mcp.services.response_enrichment import date_hints
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def _parse_flexible_date(date_str: str) -> datetime:
    """Parse date in various formats: YYYY, YYYY-MM, YYYY-MM-DD."""
    date_str = date_str.strip()

    # YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return datetime.strptime(date_str, "%Y-%m-%d")

    # YYYY-MM
    if re.match(r"^\d{4}-\d{2}$", date_str):
        return datetime.strptime(date_str + "-01", "%Y-%m-%d")

    # YYYY
    if re.match(r"^\d{4}$", date_str):
        return datetime.strptime(date_str + "-01-01", "%Y-%m-%d")

    raise ValueError(
        f"Nieprawidłowy format daty: '{date_str}'. "
        f"Obsługiwane formaty: YYYY-MM-DD (np. '2024-01-15'), "
        f"YYYY-MM (np. '2024-01', domyślnie 1. dzień), "
        f"YYYY (np. '2024', domyślnie 1 stycznia)."
    )


def register(mcp: FastMCP) -> None:
    """Register date calculation tool."""

    @mcp.tool(tags={"dates", "utility"})
    @handle_tool_errors(
        default_factory=lambda e, kw: DateOutput(
            base_date=str(kw.get("base_date", "")),
            calculated_date="",
            days_offset=0,
            months_offset=0,
            years_offset=0,
            description="",
        ),
    )
    async def calculate_legal_date(
        days: Annotated[
            str | int,
            "Liczba dni do dodania (+) lub odjęcia (-). "
            "Np. days=14 = za 14 dni, days=-14 = 14 dni temu. Domyślnie 0.",
        ] = 0,
        months: Annotated[
            str | int,
            "Liczba miesięcy do dodania (+) lub odjęcia (-). "
            "Np. months=3 = za 3 miesiące, months=-6 = 6 miesięcy temu. Domyślnie 0.",
        ] = 0,
        years: Annotated[
            str | int,
            "Liczba lat do dodania (+) lub odjęcia (-). "
            "Np. years=1 = za rok, years=-5 = 5 lat temu. Domyślnie 0.",
        ] = 0,
        base_date: Annotated[
            str | None,
            "Data bazowa. Akceptowane formaty: 'YYYY-MM-DD' (np. '2024-01-15'), "
            "'YYYY-MM' (np. '2024-01' → 2024-01-01), 'YYYY' (np. '2024' → 2024-01-01). "
            "Jeśli nie podano, używa dzisiejszej daty.",
        ] = None,
        ctx: Context = None,
    ) -> str:
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
        from dateutil.relativedelta import relativedelta  # type: ignore[import-untyped]

        assert ctx is not None

        # Normalize int params (MCP clients may send strings)
        days_int = 0
        with contextlib.suppress(ValueError, TypeError):
            days_int = int(days)
        months_int = 0
        with contextlib.suppress(ValueError, TypeError):
            months_int = int(months)
        years_int = 0
        with contextlib.suppress(ValueError, TypeError):
            years_int = int(years)

        # Parse base date or use today
        if base_date:
            base = _parse_flexible_date(base_date)
            base_date_str = base.strftime("%Y-%m-%d")
        else:
            base = datetime.now()
            base_date_str = base.strftime("%Y-%m-%d")

        # Calculate new date
        result_date = base + relativedelta(days=days_int, months=months_int, years=years_int)
        result_str = result_date.strftime("%Y-%m-%d")

        # Build description
        parts = []
        if years_int != 0:
            y = abs(years_int)
            if y == 1:
                parts.append("1 rok")
            elif 2 <= y <= 4:
                parts.append(f"{y} lata")
            else:
                parts.append(f"{y} lat")
        if months_int != 0:
            m = abs(months_int)
            if m == 1:
                parts.append("1 miesiąc")
            elif 2 <= m <= 4:
                parts.append(f"{m} miesiące")
            else:
                parts.append(f"{m} miesięcy")
        if days_int != 0:
            d = abs(days_int)
            if d == 1:
                parts.append("1 dzień")
            else:
                parts.append(f"{d} dni")

        if not parts:
            description = f"Data bazowa: {base_date_str}"
        else:
            direction = "po" if (days_int + months_int + years_int) >= 0 else "przed"
            description = f"{', '.join(parts)} {direction} {base_date_str}"

        response = EnrichedResponse(
            data=DateOutput(
                base_date=base_date_str,
                calculated_date=result_str,
                days_offset=days_int,
                months_offset=months_int,
                years_offset=years_int,
                description=description,
            ),
            hints=date_hints(),
        )

        return response.model_dump_json()
