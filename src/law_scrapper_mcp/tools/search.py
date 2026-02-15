"""Search legal acts tool."""

import contextlib
import logging
from typing import Annotated

from fastmcp import Context, FastMCP

from law_scrapper_mcp.models.enums import DetailLevel
from law_scrapper_mcp.models.tool_outputs import EnrichedResponse, SearchOutput
from law_scrapper_mcp.services.response_enrichment import search_hints
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_LIMIT = 20


def register(mcp: FastMCP) -> None:
    """Register search tool."""

    @mcp.tool(tags={"search"})
    @handle_tool_errors(
        default_factory=lambda e, kw: SearchOutput(
            results=[], total_count=0, query_summary="", returned_count=0
        ),
    )
    async def search_legal_acts(
        publisher: Annotated[
            str,
            "Kod wydawcy: 'DU' (Dziennik Ustaw) lub 'MP' (Monitor Polski). Domyślnie 'DU'.",
        ] = "DU",
        year: Annotated[str | int | None, "Rok publikacji (np. 2024)."] = None,
        keywords: Annotated[
            list[str] | None,
            "Słowa kluczowe z systemu Sejmu (logika AND — wiele słów zawęża wyniki). "
            "Aby uzyskać logikę OR, wykonaj oddzielne wyszukiwanie dla każdego słowa. "
            "Użyj get_system_metadata(category='keywords') aby poznać dostępne słowa kluczowe.",
        ] = None,
        date_from: Annotated[
            str | None,
            "Data wejścia w życie OD (YYYY-MM-DD). Filtruje akty które weszły w życie od tej daty.",
        ] = None,
        date_to: Annotated[
            str | None,
            "Data wejścia w życie DO (YYYY-MM-DD). Filtruje akty które weszły w życie do tej daty.",
        ] = None,
        title: Annotated[
            str | None,
            "Szukaj w tytule aktu (dopasowanie podciągu). Np. 'budżet', 'przeciwpożarow', 'podatek dochodowy'.",
        ] = None,
        act_type: Annotated[
            str | None,
            "Typ dokumentu (dokładne dopasowanie). Dostępne wartości: "
            "'Ustawa', 'Rozporządzenie', 'Obwieszczenie', 'Komunikat', "
            "'Uchwała', 'Zarządzenie', 'Wyrok', 'Postanowienie', 'Oświadczenie rządowe'. "
            "Użyj get_system_metadata(category='types') aby zobaczyć pełną listę.",
        ] = None,
        pub_date_from: Annotated[
            str | None,
            "Data publikacji/ogłoszenia OD (YYYY-MM-DD). Filtruje po dacie ogłoszenia w dzienniku.",
        ] = None,
        pub_date_to: Annotated[
            str | None,
            "Data publikacji/ogłoszenia DO (YYYY-MM-DD). Filtruje po dacie ogłoszenia w dzienniku.",
        ] = None,
        in_force: Annotated[
            str | bool | None,
            "Filtruj po obowiązywaniu: true = tylko akty obecnie obowiązujące, "
            "false = tylko akty nieobowiązujące, None = wszystkie.",
        ] = None,
        limit: Annotated[
            str | int | None,
            "Maksymalna liczba wyników do zwrócenia. Domyślnie 20. Przydatne do ograniczenia dużych zbiorów.",
        ] = None,
        offset: Annotated[
            str | int | None,
            "Liczba wyników do pominięcia (paginacja). Użyj z parametrem limit.",
        ] = None,
        detail_level: Annotated[
            str,
            "Poziom szczegółowości wyników: 'minimal' (ELI, tytuł, status), "
            "'standard' (+ typ, daty, obowiązywanie), 'full' (wszystkie pola). Domyślnie 'standard'.",
        ] = "standard",
        ctx: Context = None,
    ) -> str:
        """
        Wyszukaj polskie akty prawne z Dziennika Ustaw (DU) i Monitora Polskiego (MP).

        UWAGA: Wszystkie słowa kluczowe muszą wystąpić jednocześnie (logika AND).
        Szukaj jednego słowa na raz dla logiki OR.

        Kiedy użyć: Gdy znasz słowa kluczowe, tytuł, typ lub kryteria wyszukiwania.
        Kiedy NIE używać: Gdy chcesz przeglądać cały rocznik → użyj browse_acts.

        Po wyszukiwaniu możesz użyć filter_results(result_set_id=...) aby dalej zawęzić wyniki
        (np. regex na tytułach, filtr po typie dokumentu).

        Przykłady:
        - search_legal_acts(keywords=["podatek"], year=2024) - Akty podatkowe z 2024
        - search_legal_acts(act_type="Ustawa", title="zdrowotny") - Ustawy o zdrowiu
        - search_legal_acts(act_type="Rozporządzenie", year=2024, in_force=True) - Obowiązujące rozporządzenia z 2024
        - search_legal_acts(publisher="MP", year=2024, limit=10) - 10 aktów z MP z 2024
        - search_legal_acts(date_from="2024-01-01", date_to="2024-06-30") - Akty wchodzące w życie I poł. 2024
        - search_legal_acts(pub_date_from="2024-03-01", pub_date_to="2024-03-31") - Ogłoszone w marcu 2024
        - search_legal_acts(title="budżet", year=2024) - Akty budżetowe z 2024
        """
        assert ctx is not None
        search_service = ctx.request_context.lifespan_context["search_service"]
        result_store = ctx.request_context.lifespan_context["result_store"]

        # Normalize params (MCP clients may send int/bool as strings)
        year_int: int | None = None
        if year is not None:
            with contextlib.suppress(ValueError, TypeError):
                year_int = int(year)

        limit_int: int | None = None
        if limit is not None:
            with contextlib.suppress(ValueError, TypeError):
                limit_int = int(limit)

        offset_int: int | None = None
        if offset is not None:
            with contextlib.suppress(ValueError, TypeError):
                offset_int = int(offset)

        in_force_bool: bool | None = None
        if in_force is not None:
            in_force_bool = (
                in_force.lower() in ("true", "1", "yes")
                if isinstance(in_force, str)
                else bool(in_force)
            )

        # Convert detail_level string to enum
        try:
            detail_enum = DetailLevel(detail_level)
        except ValueError:
            detail_enum = DetailLevel.STANDARD

        results, total_count, query_summary = await search_service.search(
            publisher=publisher,
            year=year_int,
            keywords=keywords,
            date_from=date_from,
            date_to=date_to,
            title=title,
            act_type=act_type,
            pub_date_from=pub_date_from,
            pub_date_to=pub_date_to,
            in_force=in_force_bool,
            limit=limit_int,
            offset=offset_int,
            detail_level=detail_enum,
        )

        # Apply default limit if no explicit limit was provided
        effective_limit = limit_int if limit_int is not None else DEFAULT_SEARCH_LIMIT
        was_truncated = len(results) > effective_limit
        if was_truncated:
            results = results[:effective_limit]

        # Store results for subsequent filtering
        result_set_id = None
        if results:
            result_set_id = await result_store.store(results, query_summary, total_count)

        first_eli = results[0].eli if results else None

        response = EnrichedResponse(
            data=SearchOutput(
                results=results,
                total_count=total_count,
                query_summary=query_summary,
                returned_count=len(results),
                result_set_id=result_set_id,
            ),
            hints=search_hints(
                total_count,
                len(results) > 0,
                first_eli,
                result_set_id,
                was_truncated=was_truncated,
                applied_limit=effective_limit,
            ),
        )

        return response.model_dump_json()
