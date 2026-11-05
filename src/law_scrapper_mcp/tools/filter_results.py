"""Filter and narrow down previously retrieved search/browse results."""

import contextlib
import logging
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from law_scrapper_mcp.config import settings
from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.models.pagination import DEFAULT_ITEM_LIMIT
from law_scrapper_mcp.models.tool_outputs import (
    DocumentKind,
    EnrichedResponse,
    FilterOutput,
    ResultSetListOutput,
)
from law_scrapper_mcp.services.judgment_filters import judgment_filter_hints
from law_scrapper_mcp.services.pagination import parse_non_negative
from law_scrapper_mcp.services.pattern_matching import SUPPORTED_SYNTAX_HINT
from law_scrapper_mcp.services.response_enrichment import filter_hints, result_sets_hints
from law_scrapper_mcp.tools.annotations import READ_ONLY_LOCAL
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def register(mcp: MCPServer[AppContext]) -> None:
    """Register filter results tool."""

    @mcp.tool(
        title="Filtrowanie wyników wyszukiwania",
        annotations=READ_ONLY_LOCAL,
        meta={"tags": ["utility", "filter"]},
    )
    @handle_tool_errors
    async def filter_results(
        result_set_id: Annotated[
            str,
            Field(
                description=(
                    "Identyfikator zestawu wyników z poprzedniego wyszukiwania (np. 'rs_1'). "
                    "Zwracany przez search_legal_acts, browse_acts lub track_legal_changes w polu result_set_id, "
                    "a także przez narzędzia wyszukiwania orzeczeń modułów źródeł (zestaw orzeczeń)."
                ),
            ),
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
            Field(
                description=(
                    "Pole do przeszukania wzorcem RE2. Dla aktów: 'title' (domyślne), 'eli', 'status', 'type', "
                    "'publisher'. Dla zestawów orzeczeń: 'title' (domyślne; etykieta z sygnaturą, rodzajem, sądem "
                    "i datą), 'type', oraz tylko dla zestawów orzeczeń 'signature', 'court', 'division', "
                    "'division_code', 'excerpt'."
                ),
            ),
        ] = "title",
        type_equals: Annotated[
            str | None,
            Field(
                description=(
                    "Filtruj po typie dokumentu (dokładne dopasowanie). "
                    "Wartości dla aktów: 'Ustawa', 'Rozporządzenie', 'Obwieszczenie', 'Komunikat', "
                    "'Uchwała', 'Zarządzenie', 'Wyrok', 'Postanowienie', 'Oświadczenie rządowe'. "
                    "Wartości dla orzeczeń: 'Wyrok', 'Postanowienie', 'Uchwała', 'Zarządzenie', 'Uzasadnienie'."
                ),
            ),
        ] = None,
        status_equals: Annotated[
            str | None,
            Field(
                description=(
                    "Filtruj po statusie aktu (dokładne dopasowanie); tylko dla zestawów aktów — "
                    "dla zestawu orzeczeń zwraca błąd. "
                    "Wartości: 'akt obowiązujący', 'akt uchylony', 'akt jednorazowy', "
                    "'obowiązujący', 'uchylony', 'uznany za uchylony', 'nieobowiązujący'"
                ),
            ),
        ] = None,
        year_equals: Annotated[
            str | int | None,
            Field(description="Filtruj po roku publikacji (dokładne dopasowanie). Np. 2024."),
        ] = None,
        date_field: Annotated[
            str | None,
            Field(
                description=(
                    "Pole daty do filtrowania zakresem: 'promulgation_date' (data ogłoszenia) "
                    "lub 'effective_date' (data wejścia w życie) dla aktów; 'judgment_date' (data orzeczenia) "
                    "dla zestawów orzeczeń."
                ),
            ),
        ] = None,
        date_from: Annotated[
            str | None,
            Field(description="Początek zakresu dat (YYYY-MM-DD). Wymaga ustawienia date_field."),
        ] = None,
        date_to: Annotated[
            str | None,
            Field(description="Koniec zakresu dat (YYYY-MM-DD). Wymaga ustawienia date_field."),
        ] = None,
        sort_by: Annotated[
            str | None,
            Field(
                description=(
                    "Pole do sortowania. Dla aktów: 'title', 'year', 'pos', 'promulgation_date', "
                    "'effective_date', 'type', 'status'. Dla zestawów orzeczeń: 'title', 'signature', 'court', "
                    "'type', 'judgment_date'."
                ),
            ),
        ] = None,
        sort_desc: Annotated[
            str | bool,
            Field(description="Sortuj malejąco (True) lub rosnąco (False, domyślnie)."),
        ] = False,
        limit: Annotated[
            str | int | None,
            Field(description="Maksymalna liczba wyników na stronie odpowiedzi (domyślnie 20, maks. 100)."),
        ] = 20,
        offset: Annotated[
            str | int | None,
            Field(description="Nieujemne przesunięcie strony wyników."),
        ] = 0,
    ) -> EnrichedResponse[FilterOutput]:
        """
        Filtruj i zawężaj wyniki wcześniejszego wyszukiwania aktów prawnych lub orzeczeń.

        Działa jak grep/filtr na wynikach z search_legal_acts, browse_acts lub track_legal_changes
        oraz na zestawach orzeczeń z narzędzi wyszukiwania orzeczeń modułów źródeł.
        Wymaga result_set_id zwróconego przez te narzędzia. Przefiltrowane wyniki
        zapisywane są jako nowy zestaw (nowe result_set_id), który można filtrować dalej.

        Zestaw orzeczeń: wyniki są w polu judgments (pole results jest puste, document_kind = 'judgment'),
        a wzorzec można dopasować do pól 'signature', 'court', 'division', 'division_code', 'excerpt'.
        Filtr status_equals dotyczy tylko aktów. Orzeczenia wydziałów pracy sądów powszechnych zawęzisz
        wzorcem na kodzie wydziału: pattern="(15|18|21)$", field="division_code" (wzorzec "(15|21)$"
        pomija wydziały wyłącznie ubezpieczeń społecznych; nazwy wydziałów: field="division").

        Limit rozmiaru wejścia: pojedyncze wywołanie przetwarza maksymalnie 100 rekordów
        (wartość domyślna, konfigurowalna przez operatora). Większy zestaw kończy się
        błędem, a nie wynikiem częściowym — dzięki temu brak dopasowania zawsze oznacza
        przeszukanie całego zestawu. Zawęź wyszukiwanie przed filtrowaniem.

        Kiedy użyć: Po search_legal_acts/browse_acts/track_legal_changes aby zawęzić wyniki.
        Kiedy użyć: Po wyszukiwaniu orzeczeń, aby zawęzić zestaw orzeczeń (wydział, sąd, rodzaj, data).
        Kiedy NIE używać: Gdy potrzebujesz nowych wyników z API → użyj search_legal_acts.
        Kiedy NIE używać: Do dowodzenia, że akt nie istnieje, dopóki pole source_scope
        odpowiedzi ma wartość 'page' — filtrowane jest wtedy okno, a nie cały zbiór,
        więc pusty wynik nie rozstrzyga. Odpowiedź sygnalizuje to polem
        no_match_is_inconclusive.

        Przykłady:
        - filter_results(result_set_id="rs_1", type_equals="Rozporządzenie") - Tylko rozporządzenia
        - filter_results(result_set_id="rs_1", pattern="zdrow|apteka|lekar") - Akty związane ze zdrowiem
        - filter_results(result_set_id="rs_1", pattern="podatek|VAT", type_equals="Ustawa") - Ustawy podatkowe
        - filter_results(result_set_id="rs_1", date_field="promulgation_date", date_from="2024-01-01", date_to="2024-06-30") - Ogłoszone w I połowie 2024
        - filter_results(result_set_id="rs_1", sort_by="promulgation_date", sort_desc=True, limit=10) - 10 najnowszych na pierwszej stronie
        - filter_results(result_set_id="rs_1", pattern="\\p{L}+ o ochronie") - Wzorzec z klasą unikodową
        - filter_results(result_set_id="rs_2", pattern="(15|18|21)$", field="division_code") - Orzeczenia wydziałów pracy (zestaw orzeczeń)
        """
        result_store = get_app_context(ctx).result_store

        year_int: int | None = None
        if year_equals is not None:
            with contextlib.suppress(ValueError, TypeError):
                year_int = int(year_equals)

        limit_int = parse_non_negative(limit, name="limit", default=DEFAULT_ITEM_LIMIT)
        offset_int = parse_non_negative(offset, name="offset", default=0)

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

        build_hints = judgment_filter_hints if output.document_kind is DocumentKind.JUDGMENT else filter_hints
        return EnrichedResponse[FilterOutput](
            data=output,
            hints=build_hints(output, filter_max_records=settings.effective_filter_max_records),
        )

    @mcp.tool(
        title="Zestawy wyników w pamięci",
        annotations=READ_ONLY_LOCAL,
        meta={"tags": ["utility", "filter"]},
    )
    @handle_tool_errors
    async def list_result_sets(
        ctx: Context[AppContext],
        limit: Annotated[
            str | int | None,
            Field(description="Maksymalna liczba zestawów na stronie odpowiedzi (domyślnie 20, maks. 100)."),
        ] = 20,
        offset: Annotated[
            str | int | None,
            Field(description="Nieujemne przesunięcie strony zestawów. Domyślnie 0."),
        ] = 0,
    ) -> EnrichedResponse[ResultSetListOutput]:
        """
        Wyświetl aktywne zestawy wyników przechowywane w pamięci.

        Każde wyszukiwanie (search_legal_acts, browse_acts, track_legal_changes)
        oraz filtrowanie (filter_results) tworzy zestaw wyników z unikalnym result_set_id.
        To narzędzie pokazuje aktywne zestawy (TTL: 1h) wraz z metadanymi paginacji.

        Kiedy użyć: Aby sprawdzić jakie result_set_id są dostępne do filtrowania.
        Kiedy NIE używać: Do wyszukiwania nowych aktów → użyj search_legal_acts.

        Przykłady:
        - list_result_sets() - Pierwsza strona aktywnych zestawów
        - list_result_sets(limit=5) - Pięć najnowszych wpisów strony
        - list_result_sets(limit=5, offset=5) - Kolejna strona
        - list_result_sets(limit=100) - Wszystkie aktywne zestawy
        - list_result_sets(offset=0) - Jawne przesunięcie od początku
        """
        result_store = get_app_context(ctx).result_store

        output = await result_store.list_sets_page(limit=limit, offset=offset)

        return EnrichedResponse[ResultSetListOutput](
            data=output,
            hints=result_sets_hints(output.sets),
        )
