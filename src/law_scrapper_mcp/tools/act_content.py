"""Read content from loaded legal acts."""

import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP

from law_scrapper_mcp.context import get_app_context
from law_scrapper_mcp.models.pagination import (
    DEFAULT_ITEM_LIMIT,
    DEFAULT_SECTION_CHAR_LIMIT,
    MAX_ITEM_LIMIT,
    MAX_SECTION_CHAR_LIMIT,
    error_page_info_for_content,
)
from law_scrapper_mcp.models.tool_outputs import (
    ContentOutput,
    EnrichedResponse,
    LoadedDocumentInfo,
    LoadedDocumentListOutput,
)
from law_scrapper_mcp.services.pagination import (
    effective_limit,
    paginate_items,
    paginate_text,
    parse_non_negative,
)
from law_scrapper_mcp.services.response_enrichment import content_hints
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

logger = logging.getLogger(__name__)


def _content_error_output(_: Exception, kw: dict[str, Any]) -> ContentOutput:
    section = kw.get("section")
    return ContentOutput(
        eli=kw.get("eli", ""),
        section_id=section,
        section_title="",
        content="",
        page_info=error_page_info_for_content(section=section),
    )


def register(mcp: FastMCP) -> None:
    """Register act content reading tool."""

    @mcp.tool(tags={"analysis", "content"})
    @handle_tool_errors(default_factory=_content_error_output)
    async def read_act_content(
        eli: Annotated[
            str,
            'Identyfikator ELI aktu. Format: "{wydawca}/{rok}/{pozycja}". '
            "Wydawcy: DU (Dziennik Ustaw), MP (Monitor Polski). "
            'Przykłady: "DU/2024/1716", "MP/2023/500", "DU/2024/1". '
            "Akt MUSI być wcześniej załadowany przez get_act_details(eli=..., load_content=True).",
        ],
        section: Annotated[
            str | None,
            "Identyfikator sekcji do odczytania. Można użyć surowego ID (np. 'art_1') "
            "lub formy czytelnej (np. 'Art. 1', 'Rozdział 1') - obsługiwane jest elastyczne dopasowanie. "
            "Aby poznać dostępne section_id, użyj get_act_details(load_content=true) i sprawdź tabelę treści. "
            "Jeśli None — zwraca spis treści z dostępnymi sekcjami.",
        ] = None,
        limit: Annotated[
            str | int | None,
            "Maksymalna liczba elementów lub znaków na stronie. Domyślnie zależy od trybu odczytu.",
        ] = None,
        offset: Annotated[
            str | int,
            "Nieujemne przesunięcie początku strony. Domyślnie 0.",
        ] = 0,
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
        document_store = get_app_context(ctx).document_store

        page_offset = parse_non_negative(offset, name="offset", default=0)
        if section is None:
            page_limit = effective_limit(limit, default=DEFAULT_ITEM_LIMIT, maximum=MAX_ITEM_LIMIT)
            sections = await document_store.get_toc(eli)
            all_toc = [{"id": item.id, "title": item.title, "level": item.level} for item in sections]
            toc, page_info = paginate_items(all_toc, limit=page_limit, offset=page_offset)
            output = ContentOutput(
                eli=eli,
                section_id=None,
                section_title="Spis treści",
                content=f"Znaleziono {page_info.total_count} sekcji",
                toc=toc,
                page_info=page_info,
            )
            response = EnrichedResponse(
                data=output,
                hints=content_hints(eli, page_info.total_count > 0),
            )
        else:
            page_limit = effective_limit(
                limit,
                default=DEFAULT_SECTION_CHAR_LIMIT,
                maximum=MAX_SECTION_CHAR_LIMIT,
            )
            full_content = await document_store.get_section(eli, section)
            if full_content is None:
                raise ValueError(
                    f"Sekcja '{section}' nie znaleziona w akcie {eli}. "
                    f"Użyj read_act_content(eli='{eli}') aby zobaczyć dostępne sekcje."
                )
            content, page_info = paginate_text(full_content, limit=page_limit, offset=page_offset)
            output = ContentOutput(
                eli=eli,
                section_id=section,
                section_title=section,
                content=content,
                page_info=page_info,
            )
            response = EnrichedResponse(
                data=output,
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
        document_store = get_app_context(ctx).document_store

        raw_docs = await document_store.list_documents()
        documents = [LoadedDocumentInfo.model_validate(d) for d in raw_docs]

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
