"""Read content from loaded legal acts."""

import logging
from typing import Annotated

from fastmcp import Context, FastMCP

from law_scrapper_mcp.models.tool_outputs import (
    ContentOutput,
    EnrichedResponse,
    LoadedDocumentInfo,
    LoadedDocumentListOutput,
)
from law_scrapper_mcp.services.response_enrichment import content_hints
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    """Register act content reading tool."""

    @mcp.tool(tags={"analysis", "content"})
    @handle_tool_errors(
        default_factory=lambda e, kw: ContentOutput(
            eli=kw.get("eli", ""),
            section_id=kw.get("section"),
            section_title="",
            content="",
        ),
    )
    async def read_act_content(
        eli: Annotated[
            str,
            "Identyfikator ELI aktu. Format: \"{wydawca}/{rok}/{pozycja}\". "
            "Wydawcy: DU (Dziennik Ustaw), MP (Monitor Polski). "
            "Przykłady: \"DU/2024/1716\", \"MP/2023/500\", \"DU/2024/1\". "
            "Akt MUSI być wcześniej załadowany przez get_act_details(eli=..., load_content=True).",
        ],
        section: Annotated[
            str | None,
            "Identyfikator sekcji do odczytania. Można użyć surowego ID (np. 'art_1') "
            "lub formy czytelnej (np. 'Art. 1', 'Rozdział 1') - obsługiwane jest elastyczne dopasowanie. "
            "Aby poznać dostępne section_id, użyj get_act_details(load_content=true) i sprawdź tabelę treści. "
            "Jeśli None — zwraca spis treści z dostępnymi sekcjami.",
        ] = None,
        ctx: Context = None,
    ) -> str:
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
        assert ctx is not None
        document_store = ctx.request_context.lifespan_context["document_store"]

        if section is None:
            # Return table of contents
            sections = await document_store.get_toc(eli)
            toc = [
                {"id": s.id, "title": s.title, "level": s.level} for s in sections
            ]
            response = EnrichedResponse(
                data=ContentOutput(
                    eli=eli,
                    section_id=None,
                    section_title="Spis treści",
                    content=f"Znaleziono {len(toc)} sekcji",
                    toc=toc,
                ),
                hints=content_hints(eli, len(toc) > 0),
            )
        else:
            # Read specific section
            content = await document_store.get_section(eli, section)
            if content is None:
                error_response = EnrichedResponse(
                    data=ContentOutput(
                        eli=eli,
                        section_id=section,
                        section_title="",
                        content="",
                    ),
                    error=f"Sekcja '{section}' nie znaleziona w akcie {eli}. "
                    f"Użyj read_act_content(eli='{eli}') aby zobaczyć dostępne sekcje.",
                )
                return error_response.model_dump_json()

            response = EnrichedResponse(
                data=ContentOutput(
                    eli=eli,
                    section_id=section,
                    section_title=section,
                    content=content,
                ),
                hints=content_hints(eli, True),
            )

        return response.model_dump_json()

    @mcp.tool(tags={"utility", "content"})
    @handle_tool_errors(
        default_factory=lambda e, kw: LoadedDocumentListOutput(documents=[], count=0),
    )
    async def list_loaded_documents(
        ctx: Context = None,
    ) -> str:
        """
        Wyświetl dokumenty załadowane do pamięci (Document Store).

        Dokumenty ładowane są przez get_act_details(eli=..., load_content=True).
        Każdy dokument ma TTL 2h — po tym czasie wymaga ponownego załadowania.

        Kiedy użyć: Aby sprawdzić jakie akty są załadowane i dostępne do czytania/wyszukiwania.
        Kiedy NIE używać: Do wyszukiwania aktów → użyj search_legal_acts.

        Przykłady:
        - list_loaded_documents() - Wyświetl wszystkie załadowane dokumenty
        """
        assert ctx is not None
        document_store = ctx.request_context.lifespan_context["document_store"]

        raw_docs = await document_store.list_documents()
        documents = [LoadedDocumentInfo(**d) for d in raw_docs]

        hints = []
        if documents:
            from law_scrapper_mcp.models.tool_outputs import Hint

            hints.append(
                Hint(
                    message=f"Użyj read_act_content(eli='{documents[0].eli}') aby czytać treść.",
                    tool="read_act_content",
                    parameters={"eli": documents[0].eli},
                )
            )

        response = EnrichedResponse(
            data=LoadedDocumentListOutput(documents=documents, count=len(documents)),
            hints=hints,
        )

        return response.model_dump_json()
