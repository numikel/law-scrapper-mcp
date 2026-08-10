"""Filter and narrow down previously retrieved search/browse results."""

import contextlib
import logging
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.models.tool_outputs import (
    EnrichedResponse,
    FilterOutput,
    Hint,
    ResultSetInfo,
    ResultSetListOutput,
)
from law_scrapper_mcp.services.pattern_matching import SUPPORTED_SYNTAX_HINT
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def register(mcp: MCPServer[AppContext]) -> None:
    """Register filter results tool."""

    @mcp.tool(meta={"tags": ["utility", "filter"]})
    @handle_tool_errors
    async def filter_results(
        result_set_id: Annotated[
            str,
            "Identyfikator zestawu wyników z poprzedniego wyszukiwania (np. 'rs_1'). "
            "Zwracany przez search_legal_acts, browse_acts lub track_legal_changes w polu result_set_id.",
        ],
        ctx: Context[AppContext],
        pattern: Annotated[
            str | None,
            Field(
                description=(
                    f"Wzorzec wyszukiwania w składni RE2. Wielkość liter jest ignorowana. {SUPPORTED_SYNTAX_HINT} "
                    "Przykłady: 'zdrow|Minister Zdrowia|apteka|lekar', 'budżet.*państw', "
                    r"'transport|drogow', '\p{L}+ o ochronie'"
                ),
            ),
        ] = None,
        field: Annotated[
            str,
            "Pole do przeszukania wzorcem RE2. Dostępne: 'title' (domyślne), 'eli', 'status', 'type', 'publisher'.",
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
            "Maksymalna liczba wyników na stronie odpowiedzi (domyślnie 20, maks. 100).",
        ] = 20,
        offset: Annotated[
            str | int | None,
            "Nieujemne przesunięcie strony wyników.",
        ] = 0,
    ) -> EnrichedResponse[FilterOutput]:
        """
        Filtruj i zawężaj wyniki wcześniejszego wyszukiwania aktów prawnych.

        Działa jak grep/filtr na wynikach z search_legal_acts, browse_acts lub track_legal_changes.
        Wymaga result_set_id zwróconego przez te narzędzia. Przefiltrowane wyniki
        zapisywane są jako nowy zestaw (nowe result_set_id), który można filtrować dalej.

        Limit rozmiaru wejścia: pojedyncze wywołanie przetwarza maksymalnie 100 rekordów
        (wartość domyślna, konfigurowalna przez operatora). Większy zestaw kończy się
        błędem, a nie wynikiem częściowym — dzięki temu brak dopasowania zawsze oznacza
        przeszukanie całego zestawu. Zawęź wyszukiwanie przed filtrowaniem.

        Kiedy użyć: Po search_legal_acts/browse_acts/track_legal_changes aby zawęzić wyniki.
        Kiedy NIE używać: Gdy potrzebujesz nowych wyników z API → użyj search_legal_acts.

        Przykłady:
        - filter_results(result_set_id="rs_1", type_equals="Rozporządzenie") - Tylko rozporządzenia
        - filter_results(result_set_id="rs_1", pattern="zdrow|apteka|lekar") - Akty związane ze zdrowiem
        - filter_results(result_set_id="rs_1", pattern="podatek|VAT", type_equals="Ustawa") - Ustawy podatkowe
        - filter_results(result_set_id="rs_1", date_field="promulgation_date", date_from="2024-01-01", date_to="2024-06-30") - Ogłoszone w I połowie 2024
        - filter_results(result_set_id="rs_1", sort_by="promulgation_date", sort_desc=True, limit=10) - 10 najnowszych na pierwszej stronie
        - filter_results(result_set_id="rs_1", pattern="\\p{L}+ o ochronie") - Wzorzec z klasą unikodową
        """
        result_store = get_app_context(ctx).result_store

        year_int: int | None = None
        if year_equals is not None:
            with contextlib.suppress(ValueError, TypeError):
                year_int = int(year_equals)

        limit_int = int(limit) if limit is not None else 20
        offset_int = int(offset) if offset is not None else 0

        sort_desc_bool = sort_desc.lower() in ("true", "1", "yes") if isinstance(sort_desc, str) else bool(sort_desc)

        output = await result_store.filter_and_store(
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
            offset=offset_int,
        )

        hints = []
        if output.results:
            hints.append(
                Hint(
                    message="Użyj get_act_details aby zobaczyć szczegóły wybranego aktu.",
                    tool="get_act_details",
                    parameters={"eli": output.results[0].eli},
                )
            )
            if output.result_set_id:
                hints.append(
                    Hint(
                        message=f"Możesz dalej filtrować te wyniki używając result_set_id='{output.result_set_id}'.",
                        tool="filter_results",
                        parameters={"result_set_id": output.result_set_id},
                    )
                )

        return EnrichedResponse[FilterOutput](data=output, hints=hints)

    @mcp.tool(meta={"tags": ["utility", "filter"]})
    @handle_tool_errors
    async def list_result_sets(
        ctx: Context[AppContext],
    ) -> EnrichedResponse[ResultSetListOutput]:
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
        result_store = get_app_context(ctx).result_store

        raw_sets = await result_store.list_sets()
        sets = [ResultSetInfo(**s) for s in raw_sets]

        hints = []
        if sets:
            hints.append(
                Hint(
                    message=f"Użyj filter_results(result_set_id='{sets[0].result_set_id}') aby filtrować wyniki.",
                    tool="filter_results",
                    parameters={"result_set_id": sets[0].result_set_id},
                )
            )

        return EnrichedResponse[ResultSetListOutput](
            data=ResultSetListOutput(sets=sets, count=len(sets)),
            hints=hints,
        )
