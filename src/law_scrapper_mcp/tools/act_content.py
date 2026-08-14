"""Read content from loaded legal acts."""

import logging
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.models.tool_outputs import (
    ContentOutput,
    EnrichedResponse,
    Hint,
    LoadedDocumentInfo,
    LoadedDocumentListOutput,
)
from law_scrapper_mcp.services.response_enrichment import content_hints
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def register(mcp: MCPServer[AppContext]) -> None:
    """Register act content reading tool."""

    @mcp.tool(meta={"tags": ["analysis", "content"]})
    @handle_tool_errors
    async def read_act_content(
        eli: Annotated[
            str,
            Field(
                description=(
                    'Identyfikator ELI aktu. Format: "{wydawca}/{rok}/{pozycja}". '
                    "Wydawcy: DU (Dziennik Ustaw), MP (Monitor Polski). "
                    'Przykłady: "DU/2024/1716", "MP/2023/500", "DU/2024/1". '
                    "Akt MUSI być wcześniej załadowany przez get_act_details(eli=..., load_content=True)."
                ),
            ),
        ],
        ctx: Context[AppContext],
        section: Annotated[
            str | None,
            Field(
                description=(
                    "Identyfikator sekcji do odczytania. Można użyć surowego ID (np. 'art_1') "
                    "lub formy czytelnej (np. 'Art. 1', 'Rozdział 1') - obsługiwane jest elastyczne dopasowanie. "
                    "Aby poznać dostępne section_id, użyj get_act_details(load_content=true) i sprawdź tabelę treści. "
                    "Jeśli None — zwraca spis treści z dostępnymi sekcjami."
                ),
            ),
        ] = None,
        limit: Annotated[
            str | int | None,
            Field(description="Maksymalna liczba elementów lub znaków na stronie. Domyślnie zależy od trybu odczytu."),
        ] = None,
        offset: Annotated[
            str | int,
            Field(description="Nieujemne przesunięcie początku strony. Domyślnie 0."),
        ] = 0,
    ) -> EnrichedResponse[ContentOutput]:
        """
        Czytaj treść załadowanego aktu prawnego po sekcjach.

        WYMAGANIE: Akt musi być wcześniej załadowany za pomocą
        get_act_details(eli=..., load_content=True).

        Bez parametru section zwraca spis treści (listę sekcji).
        Z parametrem section zwraca treść wybranej sekcji.

        Kiedy użyć: Aby czytać treść konkretnej sekcji załadowanego aktu.
        Kiedy NIE używać: Aby zobaczyć metadane → użyj get_act_details.

        Przykłady:
        - read_act_content(eli="DU/2024/1692") - Spis treści (lista sekcji)
        - read_act_content(eli="DU/2024/1692", section="Art. 1") - Treść artykułu 1
        - read_act_content(eli="DU/2024/1692", section="Rozdział 1") - Treść rozdziału 1
        - read_act_content(eli="DU/2024/1692", section="Dział II") - Treść działu II
        - read_act_content(eli="MP/2024/100") - Spis treści aktu z MP
        """
        content_service = get_app_context(ctx).content_service

        output = await content_service.read(eli, section=section, limit=limit, offset=offset)

        return EnrichedResponse[ContentOutput](
            data=output,
            hints=content_hints(eli, output.section_id is not None or output.page_info.total_count > 0),
        )

    @mcp.tool(meta={"tags": ["utility", "content"]})
    @handle_tool_errors
    async def list_loaded_documents(
        ctx: Context[AppContext],
    ) -> EnrichedResponse[LoadedDocumentListOutput]:
        """
        Wyświetl dokumenty załadowane do pamięci (Document Store).

        Dokumenty ładowane są przez get_act_details(eli=..., load_content=True).
        Każdy dokument ma TTL 2h — po tym czasie wymaga ponownego załadowania.

        Kiedy użyć: Aby sprawdzić jakie akty są załadowane i dostępne do czytania/wyszukiwania.
        Kiedy NIE używać: Do wyszukiwania aktów → użyj search_legal_acts.

        Przykłady:
        - list_loaded_documents() - Wyświetl wszystkie załadowane dokumenty
        """
        document_store = get_app_context(ctx).document_store

        raw_docs = await document_store.list_documents()
        documents = [LoadedDocumentInfo.model_validate(d) for d in raw_docs]

        hints = []
        if documents:
            hints.append(
                Hint(
                    message=f"Użyj read_act_content(eli='{documents[0].eli}') aby czytać treść.",
                    tool="read_act_content",
                    parameters={"eli": documents[0].eli},
                )
            )

        return EnrichedResponse[LoadedDocumentListOutput](
            data=LoadedDocumentListOutput(documents=documents, count=len(documents)),
            hints=hints,
        )
