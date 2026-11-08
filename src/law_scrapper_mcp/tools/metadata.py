"""System metadata tool for retrieving keywords, publishers, statuses, types, institutions."""

import logging
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.models.enums import MetadataCategory
from law_scrapper_mcp.models.tool_outputs import EnrichedResponse, MetadataOutput
from law_scrapper_mcp.services.response_enrichment import metadata_hints
from law_scrapper_mcp.tools.annotations import READ_ONLY_REMOTE
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def register(mcp: MCPServer[AppContext]) -> None:
    """Register metadata tool."""

    @mcp.tool(
        title="Metadane systemu ELI",
        annotations=READ_ONLY_REMOTE,
        meta={"tags": ["metadata"]},
    )
    @handle_tool_errors
    async def get_system_metadata(
        ctx: Context[AppContext],
        category: Annotated[
            str,
            Field(
                description=(
                    "Kategoria metadanych: 'keywords' (słowa kluczowe do wyszukiwania), "
                    "'publishers' (wydawcy: DU, MP oraz kody i nazwy dodane przez aktywne moduły źródeł), "
                    "'statuses' (statusy aktów), "
                    "'types' (typy dokumentów: Ustawa, Rozporządzenie itp.), "
                    "'institutions' (instytucje wydające), "
                    "'coverage' (zasięg źródeł: najnowszy dokument, dostępne roczniki; pierwsze zapytanie dnia "
                    "może potrwać dłużej, bo mierzy zasięg u źródła), "
                    "'all' (wszystkie kategorie, także 'coverage'). "
                    "Domyślnie 'all'."
                ),
            ),
        ] = "all",
        limit: Annotated[
            str | int | None,
            Field(
                description="Maksymalna liczba wartości metadanych na stronie odpowiedzi (domyślnie 20, maks. 100).",
            ),
        ] = 20,
        offset: Annotated[
            str | int | None,
            Field(description="Nieujemne przesunięcie strony metadanych."),
        ] = 0,
    ) -> EnrichedResponse[MetadataOutput]:
        """
        Pobierz metadane systemu aktów prawnych.

        Zwraca dostępne słowa kluczowe, wydawców, typy dokumentów, statusy i instytucje,
        które można wykorzystać do filtrowania i wyszukiwania aktów prawnych. Kategoria 'coverage'
        mówi, co każde aktywne źródło faktycznie obejmuje (najnowszy dokument, liczba pozycji,
        dostępne roczniki), więc brak wyniku można odróżnić od luki w danych.

        Kiedy użyć:
        - Potrzebujesz słów kluczowych, typów, statusów, instytucji lub listy wydawców do filtrów.
        - Wyszukiwanie zwróciło mało lub zero wyników i chcesz sprawdzić, czy źródło w ogóle
          obejmuje dany rocznik lub okres → category="coverage".
        - Chcesz poznać świeżość źródła przed poleganiem na nim (data i odnośnik najnowszego dokumentu).
        Uwaga: pierwsze zapytanie danego dnia o 'coverage' może potrwać dłużej, bo mierzy zasięg
        u źródła (wynik jest pamiętany przez dobę). Pierwsze 'all' danego dnia kontaktuje hosty
        wszystkich aktywnych źródeł (do ok. 32 żądań), więc gdy chodzi tylko o jedną kategorię,
        podaj ją wprost zamiast 'all'.

        Kiedy NIE używać:
        - Szukasz konkretnego aktu → użyj search_legal_acts.
        - Chcesz przeczytać treść aktu → użyj get_act_details i read_act_content.

        Przykłady:
        - get_system_metadata(category="keywords") - Dostępne słowa kluczowe do wyszukiwania
        - get_system_metadata(category="types") - Typy dokumentów (Ustawa, Rozporządzenie itp.)
        - get_system_metadata(category="publishers") - Wydawcy (DU, MP i dzienniki z aktywnych modułów źródeł)
        - get_system_metadata(category="statuses") - Statusy aktów (obowiązujący, uchylony itp.)
        - get_system_metadata(category="coverage") - Zasięg źródeł (najnowszy dokument, roczniki)
        - get_system_metadata(category="all") - Wszystkie kategorie metadanych
        """
        metadata_service = get_app_context(ctx).metadata_service

        try:
            category_enum = MetadataCategory(category)
        except ValueError:
            category_enum = MetadataCategory.ALL

        output = await metadata_service.get_metadata_page(
            category_enum,
            limit=limit,
            offset=offset,
        )

        return EnrichedResponse[MetadataOutput](
            data=output,
            hints=metadata_hints(
                category_enum.value,
                output.failed_categories,
                publisher_sources=metadata_service.has_sources,
                coverage_fallback=any(item.get("origin") == "fallback" for item in output.metadata.get("coverage", [])),
            ),
        )
