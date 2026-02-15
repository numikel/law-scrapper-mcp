"""Law Scrapper MCP Server - Main entry point."""

import logging
from contextlib import asynccontextmanager

from fastmcp import FastMCP

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.circuit_breaker import CircuitBreaker
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.config import settings
from law_scrapper_mcp.logging_config import setup_logging
from law_scrapper_mcp.services.act_service import ActService
from law_scrapper_mcp.services.changes_service import ChangesService
from law_scrapper_mcp.services.content_processor import ContentProcessor
from law_scrapper_mcp.services.document_store import DocumentStore
from law_scrapper_mcp.services.metadata_service import MetadataService
from law_scrapper_mcp.services.result_store import ResultStore
from law_scrapper_mcp.services.search_service import SearchService
from law_scrapper_mcp.tools import register_all_tools

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(server):
    """Initialize and cleanup server resources."""
    logger.info("Starting Law Scrapper MCP Server v%s", settings.server_version)

    circuit_breaker = CircuitBreaker(
        failure_threshold=settings.circuit_breaker_threshold,
        recovery_timeout=settings.circuit_breaker_recovery_timeout,
        half_open_max_calls=settings.circuit_breaker_half_open_max_calls,
    )
    cache = TTLCache(max_entries=settings.cache_max_entries)
    client = SejmApiClient(
        cache=cache,
        timeout=settings.api_timeout,
        max_concurrent=settings.api_max_concurrent,
        circuit_breaker=circuit_breaker,
    )
    await client.start()

    document_store = DocumentStore(
        max_documents=settings.doc_store_max_documents,
        max_size_bytes=settings.doc_store_max_size_bytes,
        ttl=settings.doc_store_ttl,
    )
    content_processor = ContentProcessor()

    result_store = ResultStore()
    metadata_service = MetadataService(client)
    search_service = SearchService(client)
    act_service = ActService(client, document_store, content_processor)
    changes_service = ChangesService(client)

    yield {
        "client": client,
        "cache": cache,
        "document_store": document_store,
        "content_processor": content_processor,
        "result_store": result_store,
        "metadata_service": metadata_service,
        "search_service": search_service,
        "act_service": act_service,
        "changes_service": changes_service,
    }

    await client.close()
    await cache.clear()
    logger.info("Law Scrapper MCP Server stopped")


app = FastMCP(
    name=settings.server_name,
    version=settings.server_version,
    instructions="""Jesteś specjalistycznym asystentem do analizy polskiego prawa.
Odpowiadaj użytkownikowi w jego języku. Dane z narzędzi (tytuły aktów, statusy, typy) są po polsku.

DOSTĘPNE NARZĘDZIA (13):

1. WYSZUKIWANIE I PRZEGLĄDANIE:
   - search_legal_acts — wyszukuj akty po słowach kluczowych, typie (Ustawa, Rozporządzenie itp.), datach, statusie
   - browse_acts — przeglądaj wszystkie akty z danego roku i wydawcy
   - filter_results — filtruj wyniki wyszukiwania wzorcem regex, typem, datami (wymaga result_set_id z search/browse)

2. ANALIZA AKTÓW:
   - get_act_details — szczegóły aktu (status, daty, spis treści). Użyj load_content=True aby załadować treść
   - read_act_content — czytaj sekcje załadowanego aktu (Art., Rozdział)
   - search_in_act — szukaj terminów w załadowanym akcie
   - compare_acts — porównaj metadane dwóch aktów (tytuły, statusy, daty, słowa kluczowe)

3. POWIĄZANIA I ZMIANY:
   - analyze_act_relationships — powiązania między aktami (co zmienia, podstawa prawna, akty uchylone)
   - track_legal_changes — śledzenie nowych aktów/zmian w zakresie dat

4. NARZĘDZIA POMOCNICZE:
   - get_system_metadata — dostępne słowa kluczowe, typy dokumentów, statusy, wydawcy
   - calculate_legal_date — obliczanie terminów prawnych (dni/miesiące/lata od daty)
   - list_result_sets — wyświetl aktywne zestawy wyników w pamięci
   - list_loaded_documents — wyświetl załadowane dokumenty w pamięci

WORKFLOW — CZYTANIE TREŚCI AKTU:
1. get_act_details(eli="DU/2024/1692", load_content=True) → załaduj akt
2. read_act_content(eli="DU/2024/1692") → spis treści
3. read_act_content(eli="DU/2024/1692", section="Art. 1") → czytaj sekcję
4. search_in_act(eli="DU/2024/1692", query="termin") → szukaj w treści

WORKFLOW — ZAAWANSOWANE WYSZUKIWANIE:
1. search_legal_acts(year=2024, act_type="Rozporządzenie") → szerokie wyszukiwanie
2. filter_results(result_set_id="rs_1", pattern="zdrow|apteka|lekar") → zawęź regex na tytule
3. get_act_details(eli=...) → szczegóły wybranych aktów

WORKFLOW — ANALIZA ZMIAN AKTU:
1. search_legal_acts(title="ochrona przeciwpożarowa") → znajdź akt
2. analyze_act_relationships(eli=...) → sprawdź co go zmienia
3. get_act_details(eli=<zmieniający akt>, load_content=True) → załaduj treść zmiany

WORKFLOW — MONITORING ZMIAN PRAWNYCH:
1. track_legal_changes(date_from="2025-01-01", keywords=["podatek"]) → nowe akty podatkowe
2. filter_results(result_set_id=..., type_equals="Ustawa") → tylko ustawy
3. get_act_details(eli=...) → szczegóły wybranego aktu

WORKFLOW — PORÓWNYWANIE AKTÓW:
1. search_legal_acts(title="ochrona danych") → znajdź akty
2. compare_acts(eli_a="DU/2018/1000", eli_b="DU/2024/1692") → porównaj metadane
3. get_act_details(eli=..., load_content=True) → załaduj treść wybranego aktu

WORKFLOW — OBLICZANIE TERMINÓW:
1. calculate_legal_date(days=14, base_date="2025-02-01") → termin odwołania
2. search_legal_acts(title="kodeks postępowania administracyjnego") → znajdź KPA

UWAGI:
- Identyfikator ELI: wydawca/rok/pozycja (np. DU/2024/1692, MP/2023/500)
- Wydawcy: DU = Dziennik Ustaw, MP = Monitor Polski
- Słowa kluczowe API używają logiki AND. Dla OR szukaj każdego osobno.
- Każda odpowiedź zawiera 'hints' z sugerowanymi kolejnymi krokami.
- Dane w systemie (typy, statusy, słowa kluczowe) są po polsku.""",
    lifespan=lifespan,
)

register_all_tools(app)


def main():
    """Entry point for the server."""
    setup_logging(settings.log_level, settings.log_format)

    if settings.transport == "streamable-http":
        import uvicorn
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Mount, Route

        async def health(_request: Request) -> JSONResponse:
            return JSONResponse({
                "status": "ok",
                "version": settings.server_version,
                "server": settings.server_name,
            })

        starlette_app = Starlette(
            routes=[
                Route("/health", health),
                Mount("/", app=app.http_app(path="/mcp")),
            ],
        )
        uvicorn.run(starlette_app, host=settings.host, port=settings.port)
    else:
        app.run(transport="stdio")


if __name__ == "__main__":
    main()
