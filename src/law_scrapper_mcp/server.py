"""Law Scrapper MCP Server - Main entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

# Imported at module level (unlike the SDK's own lazy in-method import) so
# `server_module.uvicorn` is a patchable attribute for tests, at the cost of a
# small startup-time hit even on the `stdio` path.
import uvicorn
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from law_scrapper_mcp.auth import build_auth
from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.circuit_breaker import CircuitBreaker
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.config import log_pattern_limit_clamping, log_remote_bind_warning, settings
from law_scrapper_mcp.context import AppContext
from law_scrapper_mcp.http.rate_limit import ExemptPathCredentialStripper, RateLimitMiddleware
from law_scrapper_mcp.logging_config import setup_logging
from law_scrapper_mcp.services.act_service import ActService
from law_scrapper_mcp.services.changes_service import ChangesService
from law_scrapper_mcp.services.comparison_service import ComparisonService
from law_scrapper_mcp.services.content_processor import ContentProcessor
from law_scrapper_mcp.services.content_service import ContentService
from law_scrapper_mcp.services.date_service import DateService
from law_scrapper_mcp.services.document_store import DocumentStore
from law_scrapper_mcp.services.metadata_service import MetadataService
from law_scrapper_mcp.services.relationship_service import RelationshipService
from law_scrapper_mcp.services.result_store import ResultStore
from law_scrapper_mcp.services.search_service import SearchService
from law_scrapper_mcp.tools import register_all_tools

logger = logging.getLogger(__name__)


def build_transport_security() -> TransportSecuritySettings:
    """Host/Origin allowlist assembled from configuration (F18).

    The SDK only auto-enables DNS-rebinding protection for a literal loopback
    `host`, so this is passed explicitly and stays on regardless of the bind
    address. The defaults reproduce the constant this replaced; widening them
    is refused at startup unless authentication is configured (D6).
    """
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(settings.allowed_hosts),
        allowed_origins=list(settings.allowed_origins),
    )


class _HealthState:
    """Bridges `lifespan` resources to the operational `/health` route.

    `custom_route` hands its handler a `Request` and nothing else
    (mcp/server/mcpserver/server.py:975-1013), and the MCP `lifespan` serves the
    protocol session, not endpoints living beside it — so no channel connects
    the two. The bridge is built deliberately and kept narrow: it holds the
    breaker alone rather than the whole `AppContext`, it is written in exactly
    one place, and it is cleared by the same `finally` that closes the client.

    An empty handle is not an edge case to hide: it means the process accepts
    connections but has not finished initialising, which is worth reporting.
    """

    def __init__(self) -> None:
        self._circuit_breaker: CircuitBreaker | None = None

    def set(self, circuit_breaker: CircuitBreaker) -> None:
        self._circuit_breaker = circuit_breaker

    def clear(self) -> None:
        self._circuit_breaker = None

    def snapshot(self) -> dict[str, object]:
        if self._circuit_breaker is None:
            return {"circuit_state": "unknown"}
        return {
            "circuit_state": str(self._circuit_breaker.state),
            "failure_count": self._circuit_breaker.failure_count,
        }


_health_state = _HealthState()


SERVER_INSTRUCTIONS = """Jesteś specjalistycznym asystentem do analizy polskiego prawa.
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
- Dane w systemie (typy, statusy, słowa kluczowe) są po polsku."""


@asynccontextmanager
async def lifespan(_server: MCPServer[AppContext]) -> AsyncIterator[AppContext]:
    """Initialize and cleanup server resources."""
    logger.info("Starting Law Scrapper MCP Server v%s", settings.server_version)
    log_pattern_limit_clamping(settings, logger)

    circuit_breaker = CircuitBreaker(
        failure_threshold=settings.circuit_breaker_threshold,
        recovery_timeout=settings.circuit_breaker_recovery_timeout,
        half_open_max_calls=settings.circuit_breaker_half_open_max_calls,
    )
    _health_state.set(circuit_breaker)
    cache = TTLCache(max_entries=settings.cache_max_entries)
    client = SejmApiClient(
        cache=cache,
        timeout=settings.api_timeout,
        max_concurrent=settings.api_max_concurrent,
        circuit_breaker=circuit_breaker,
        max_attempts=settings.api_max_attempts,
        retry_budget=settings.api_retry_budget,
        user_agent=settings.user_agent,
    )
    await client.start()

    document_store = DocumentStore(
        max_documents=settings.doc_store_max_documents,
        max_size_bytes=settings.doc_store_max_size_bytes,
        ttl=settings.doc_store_ttl,
    )
    content_processor = ContentProcessor()

    result_store = ResultStore(
        max_pattern_length=settings.effective_max_pattern_length,
        pattern_length_limit_clamped=settings.max_pattern_length_was_clamped,
        max_records=settings.effective_filter_max_records,
    )
    metadata_service = MetadataService(client)
    search_service = SearchService(client, result_store)
    act_service = ActService(client, document_store, content_processor)
    content_service = ContentService(document_store)
    changes_service = ChangesService(client, result_store)
    comparison_service = ComparisonService(act_service)
    relationship_service = RelationshipService(client)
    date_service = DateService()

    context = AppContext(
        client=client,
        cache=cache,
        document_store=document_store,
        content_processor=content_processor,
        result_store=result_store,
        metadata_service=metadata_service,
        search_service=search_service,
        act_service=act_service,
        content_service=content_service,
        changes_service=changes_service,
        comparison_service=comparison_service,
        relationship_service=relationship_service,
        date_service=date_service,
    )

    try:
        yield context
    finally:
        # Cleared first: if `client.close()` raised, a populated handle would
        # let `/health` claim a live breaker after the lifespan had ended.
        _health_state.clear()
        await client.close()
        await cache.clear()
        logger.info("Law Scrapper MCP Server stopped")


_auth_settings, _token_verifier = build_auth(settings)

app = MCPServer[AppContext](
    settings.server_name,
    version=settings.server_version,
    instructions=SERVER_INSTRUCTIONS,
    lifespan=lifespan,
    auth=_auth_settings,
    token_verifier=_token_verifier,
)

register_all_tools(app)


@app.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    """Liveness probe for Docker and monitoring.

    Always 200 while the process answers. A restart cannot repair an outage of
    api.sejm.gov.pl, so reporting one as process ill-health would be a lie to
    the orchestrator and — under `restart: unless-stopped` — active harm.
    Degradation is reported in the body, for humans and monitoring to read.
    """
    return JSONResponse(
        {
            "status": "ok",
            "version": settings.server_version,
            "server": settings.server_name,
            "upstream": _health_state.snapshot(),
        }
    )


def build_http_app() -> ASGIApp:
    """Build the ASGI app served over Streamable HTTP.

    Mirrors `MCPServer.run_streamable_http_async`
    (mcp/server/mcpserver/server.py:1070-1089) minus the uvicorn wiring, which
    this project owns so that `timeout_graceful_shutdown` can be set at all.

    Re-check this function against that SDK method on every `mcp` upgrade: a
    changed `streamable_http_app()` signature would surface here first. Since
    v4.0.0 that includes the auth wiring — `auth` and `token_verifier` are read
    off the server instance (server.py:1241), not passed here.
    """
    http_app: ASGIApp = app.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        transport_security=build_transport_security(),
        host=settings.host,
    )
    if not settings.rate_limit_enabled:
        # Still stripped: the credential must not reach the verifier on an
        # unmetered path regardless of whether request budgets are switched on.
        return ExemptPathCredentialStripper(http_app)
    # Wrapping from the outside means the cheapest check runs first: the actual
    # order is rate limiting → authentication → Host/Origin validation (D13).
    # `RequireAuthMiddleware` wraps the `/mcp` route directly and is reached
    # before Host/Origin validation, which sits deeper inside the streamable
    # transport — a consequence of the SDK's own layering, not a choice this
    # project makes. The SDK offers no injection point to reorder it.
    return ExemptPathCredentialStripper(
        RateLimitMiddleware(
            http_app,
            requests=settings.rate_limit_requests,
            window=settings.rate_limit_window,
            burst=settings.rate_limit_burst,
            trusted_proxies=settings.trusted_proxies,
        )
    )


def build_uvicorn_config() -> uvicorn.Config:
    """Assemble the uvicorn configuration for the HTTP transport."""
    return uvicorn.Config(
        build_http_app(),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        # uvicorn's knob is whole seconds; the setting is a float so that it
        # reads like the other timeouts in `Settings`.
        timeout_graceful_shutdown=int(settings.shutdown_grace),
        # Off unconditionally, against uvicorn's default of True.
        # ProxyHeadersMiddleware wraps the app from the outside and writes the
        # `X-Forwarded-For` value into `scope["client"]` *without validating it
        # as an address*, so `_client_key` would take it as the peer and never
        # reach its own address check. One host inside a trusted range could
        # then mint a bucket per request with forged keys and never be
        # throttled — the criterion 11 bypass, reintroduced above the layer
        # meant to prevent it.
        #
        # Tying this to `trusted_proxies` was tried and rejected for exactly
        # that reason: it switched uvicorn's unvalidated rewrite on together
        # with our validated one, and uvicorn's runs first. `_client_key` owns
        # `X-Forwarded-For` end to end instead. Nothing else needs the rewrite —
        # the SDK builds its auth URLs from configured settings, never from the
        # request — so the only cost is that uvicorn's access log records the
        # proxy's address rather than the client's.
        #
        # `forwarded_allow_ips` is passed explicitly even though the middleware
        # is off, so the bare FORWARDED_ALLOW_IPS environment variable, which
        # uvicorn reads outside this project's namespace
        # (uvicorn/config.py:336-337), cannot widen anything unseen.
        proxy_headers=False,
        forwarded_allow_ips=[],
        # No route here speaks WebSocket, and leaving uvicorn's "auto" in place
        # makes that depend on whether an optional package happens to be
        # installed. It matters because the two middlewares below only handle
        # `http` scopes, while starlette's AuthenticationMiddleware also handles
        # `websocket` and calls the verifier on it: an upgrade request would
        # reach `verify_token` past both the budget and the credential strip.
        # Today `websockets` is absent so uvicorn degrades the upgrade to plain
        # http, but that is an accident of the dependency tree, not a decision.
        ws="none",
    )


def run_streamable_http() -> None:
    """Serve the MCP app over Streamable HTTP with a controlled shutdown window."""
    uvicorn.Server(build_uvicorn_config()).run()


def main():
    """Entry point for the server."""
    setup_logging(settings.log_level, settings.log_format)
    log_remote_bind_warning(settings, logger)
    if settings.transport == "streamable-http":
        run_streamable_http()
    else:
        app.run(transport="stdio")


if __name__ == "__main__":
    main()
