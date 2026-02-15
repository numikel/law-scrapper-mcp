"""Filter and narrow down previously retrieved search/browse results."""

import contextlib
import logging
from typing import Annotated

from fastmcp import Context, FastMCP

from law_scrapper_mcp.models.tool_outputs import (
    EnrichedResponse,
    FilterOutput,
    ResultSetInfo,
    ResultSetListOutput,
)
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    """Register filter results tool."""

    @mcp.tool(tags={"utility", "filter"})
    @handle_tool_errors(
        default_factory=lambda e, kw: FilterOutput(
            source_result_set_id=kw.get("result_set_id", ""),
            results=[],
            original_count=0,
            filtered_count=0,
        ),
    )
    async def filter_results(
        result_set_id: Annotated[
            str,
            "Identyfikator zestawu wyników z poprzedniego wyszukiwania (np. 'rs_1'). "
            "Zwracany przez search_legal_acts, browse_acts lub track_legal_changes w polu result_set_id.",
        ],
        pattern: Annotated[
            str | None,
            "Wzorzec regex do przeszukania pola (obsługuje OR: 'podatek|VAT|akcyza'). "
            "Wielkość liter jest ignorowana. Przykłady: "
            "'zdrow|Minister Zdrowia|apteka|lekar', 'budżet.*państw', 'transport|drogow'",
        ] = None,
        field: Annotated[
            str,
            "Pole do przeszukania wzorcem regex. Dostępne: 'title' (domyślne), 'eli', 'status', 'type', 'publisher'.",
        ] = "title",
        type_equals: Annotated[
            str | None,
            "Filtruj po typie dokumentu (dokładne dopasowanie). "
            "Wartości: 'Ustawa', 'Rozporządzenie', 'Obwieszczenie', 'Komunikat', "
            "'Uchwała', 'Zarządzenie', 'Wyrok', 'Postanowienie', 'Oświadczenie rządowe'",
        ] = None,
        status_equals: Annotated[
            str | None,
            "Filtruj po statusie aktu (dokładne dopasowanie). "
            "Wartości: 'akt obowiązujący', 'akt uchylony', 'akt jednorazowy', "
            "'obowiązujący', 'uchylony', 'uznany za uchylony', 'nieobowiązujący'",
        ] = None,
        year_equals: Annotated[
            str | int | None,
            "Filtruj po roku publikacji (dokładne dopasowanie). Np. 2024.",
        ] = None,
        date_field: Annotated[
            str | None,
            "Pole daty do filtrowania zakresem: 'promulgation_date' (data ogłoszenia) "
            "lub 'effective_date' (data wejścia w życie).",
        ] = None,
        date_from: Annotated[
            str | None,
            "Początek zakresu dat (YYYY-MM-DD). Wymaga ustawienia date_field.",
        ] = None,
        date_to: Annotated[
            str | None,
            "Koniec zakresu dat (YYYY-MM-DD). Wymaga ustawienia date_field.",
        ] = None,
        sort_by: Annotated[
            str | None,
            "Pole do sortowania: 'title', 'year', 'pos', 'promulgation_date', 'effective_date', 'type', 'status'.",
        ] = None,
        sort_desc: Annotated[
            str | bool,
            "Sortuj malejąco (True) lub rosnąco (False, domyślnie).",
        ] = False,
        limit: Annotated[
            str | int | None,
            "Maksymalna liczba wyników do zwrócenia.",
        ] = None,
        ctx: Context = None,
    ) -> str:
        """
        Filtruj i zawężaj wyniki wcześniejszego wyszukiwania aktów prawnych.

        Działa jak grep/filtr na wynikach z search_legal_acts, browse_acts lub track_legal_changes.
        Wymaga result_set_id zwróconego przez te narzędzia. Przefiltrowane wyniki
        zapisywane są jako nowy zestaw (nowe result_set_id), który można filtrować dalej.

        Kiedy użyć: Po search_legal_acts/browse_acts/track_legal_changes aby zawęzić wyniki.
        Kiedy NIE używać: Gdy potrzebujesz nowych wyników z API → użyj search_legal_acts.

        Przykłady:
        - filter_results(result_set_id="rs_1", type_equals="Rozporządzenie") - Tylko rozporządzenia
        - filter_results(result_set_id="rs_1", pattern="zdrow|apteka|lekar") - Akty związane ze zdrowiem
        - filter_results(result_set_id="rs_1", pattern="podatek|VAT", type_equals="Ustawa") - Ustawy podatkowe
        - filter_results(result_set_id="rs_1", date_field="promulgation_date", date_from="2024-01-01", date_to="2024-06-30") - Ogłoszone w I połowie 2024
        - filter_results(result_set_id="rs_1", sort_by="promulgation_date", sort_desc=True, limit=10) - 10 najnowszych
        """
        assert ctx is not None
        result_store = ctx.request_context.lifespan_context["result_store"]

        # Normalize params (MCP clients may send int/bool as strings)
        year_int: int | None = None
        if year_equals is not None:
            with contextlib.suppress(ValueError, TypeError):
                year_int = int(year_equals)

        limit_int: int | None = None
        if limit is not None:
            with contextlib.suppress(ValueError, TypeError):
                limit_int = int(limit)

        sort_desc_bool = (
            sort_desc.lower() in ("true", "1", "yes")
            if isinstance(sort_desc, str)
            else bool(sort_desc)
        )

        filtered, original_count = await result_store.filter_results(
            result_set_id,
            pattern=pattern,
            field=field,
            type_equals=type_equals,
            status_equals=status_equals,
            year_equals=year_int,
            date_field=date_field,
            date_from=date_from,
            date_to=date_to,
            sort_by=sort_by,
            sort_desc=sort_desc_bool,
            limit=limit_int,
        )

        # Store filtered results as a new set for potential chaining
        new_set_id = None
        if filtered:
            filters_desc = _build_filters_description(
                pattern=pattern,
                field=field,
                type_equals=type_equals,
                status_equals=status_equals,
                year_equals=year_int,
                date_field=date_field,
                date_from=date_from,
                date_to=date_to,
            )
            new_set_id = await result_store.store(
                results=filtered,
                query_summary=f"filtered({result_set_id}): {filters_desc}",
                total_count=len(filtered),
            )

        filters_applied = {}
        if pattern:
            filters_applied["pattern"] = pattern
            filters_applied["field"] = field
        if type_equals:
            filters_applied["type_equals"] = type_equals
        if status_equals:
            filters_applied["status_equals"] = status_equals
        if year_int:
            filters_applied["year_equals"] = year_int
        if date_field:
            filters_applied["date_field"] = date_field
            if date_from:
                filters_applied["date_from"] = date_from
            if date_to:
                filters_applied["date_to"] = date_to
        if sort_by:
            filters_applied["sort_by"] = sort_by
            filters_applied["sort_desc"] = sort_desc_bool
        if limit_int:
            filters_applied["limit"] = limit_int

        hints = []
        if filtered:
            from law_scrapper_mcp.models.tool_outputs import Hint

            hints.append(
                Hint(
                    message="Użyj get_act_details aby zobaczyć szczegóły wybranego aktu.",
                    tool="get_act_details",
                    parameters={"eli": filtered[0].eli},
                )
            )
            if new_set_id:
                hints.append(
                    Hint(
                        message=f"Możesz dalej filtrować te wyniki używając result_set_id='{new_set_id}'.",
                        tool="filter_results",
                        parameters={"result_set_id": new_set_id},
                    )
                )

        response = EnrichedResponse(
            data=FilterOutput(
                source_result_set_id=result_set_id,
                result_set_id=new_set_id,
                results=filtered,
                original_count=original_count,
                filtered_count=len(filtered),
                filters_applied=filters_applied,
            ),
            hints=hints,
        )

        return response.model_dump_json()


    @mcp.tool(tags={"utility", "filter"})
    @handle_tool_errors(
        default_factory=lambda e, kw: ResultSetListOutput(sets=[], count=0),
    )
    async def list_result_sets(
        ctx: Context = None,
    ) -> str:
        """
        Wyświetl aktywne zestawy wyników przechowywane w pamięci.

        Każde wyszukiwanie (search_legal_acts, browse_acts, track_legal_changes)
        oraz filtrowanie (filter_results) tworzy zestaw wyników z unikalnym result_set_id.
        To narzędzie pokazuje wszystkie aktywne zestawy (TTL: 1h).

        Kiedy użyć: Aby sprawdzić jakie result_set_id są dostępne do filtrowania.
        Kiedy NIE używać: Do wyszukiwania nowych aktów → użyj search_legal_acts.

        Przykłady:
        - list_result_sets() - Wyświetl wszystkie aktywne zestawy wyników
        """
        assert ctx is not None
        result_store = ctx.request_context.lifespan_context["result_store"]

        raw_sets = await result_store.list_sets()
        sets = [ResultSetInfo(**s) for s in raw_sets]

        hints = []
        if sets:
            from law_scrapper_mcp.models.tool_outputs import Hint

            hints.append(
                Hint(
                    message=f"Użyj filter_results(result_set_id='{sets[0].result_set_id}') aby filtrować wyniki.",
                    tool="filter_results",
                    parameters={"result_set_id": sets[0].result_set_id},
                )
            )

        response = EnrichedResponse(
            data=ResultSetListOutput(sets=sets, count=len(sets)),
            hints=hints,
        )

        return response.model_dump_json()


def _build_filters_description(**kwargs: str | int | None) -> str:
    """Build a human-readable description of applied filters."""
    parts = []
    for key, value in kwargs.items():
        if value is not None:
            parts.append(f"{key}={value}")
    return " | ".join(parts) if parts else "no filters"
