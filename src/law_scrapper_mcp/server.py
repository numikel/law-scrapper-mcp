"""Law Scrapper MCP Server - Main entry point."""

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from weakref import WeakKeyDictionary

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
from law_scrapper_mcp.client.egress import EgressDefaults, EgressHub
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
from law_scrapper_mcp.source_modules.contract import LoadedModule, ModuleHost, SourceModule, SourceModuleError
from law_scrapper_mcp.source_modules.discovery import Discovery, discover, registered_tool_names
from law_scrapper_mcp.source_modules.instructions import compose_instructions
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
    (mcp/server/mcpserver/server.py:1022-1060), and the MCP `lifespan` serves the
    protocol session, not endpoints living beside it — so no channel connects
    the two. The bridge is built deliberately and kept narrow: it holds the
    breaker alone rather than the whole `AppContext`, it is written in exactly
    one place, and it is cleared by the same `finally` that closes the client.

    An empty handle is not an edge case to hide: it means the process accepts
    connections but has not finished initialising, which is worth reporting.
    """

    def __init__(self) -> None:
        self._circuit_breaker: CircuitBreaker | None = None
        self._hub: EgressHub | None = None

    def set(self, circuit_breaker: CircuitBreaker, hub: EgressHub | None = None) -> None:
        self._circuit_breaker = circuit_breaker
        self._hub = hub

    def clear(self) -> None:
        self._circuit_breaker = None
        self._hub = None

    def sources(self) -> list[dict[str, str]]:
        """Every active source by name with its circuit state, in registration order.

        Name and state only, on purpose: the route is unauthenticated, so nothing about a
        source beyond whether it is currently failing may be read from here. Empty before the
        lifespan has registered anything and after it has ended.
        """
        if self._hub is None:
            return []
        return [{"name": state.name, "circuit_state": str(state.circuit_state)} for state in self._hub.source_states()]

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

# Tools the core registers itself. Instructions are composed before the server exists, so the count
# cannot be read off a live server; `tests/unit/test_source_module_server.py` pins it to the real
# tool list and to the header of `SERVER_INSTRUCTIONS`.
CORE_TOOL_COUNT = 13

# The module set each server was built with. `lifespan(_server)` keeps its plain signature (a test
# reads its source), so it finds its modules here instead of in a closure. Weak keys: a throw-away
# server built by a test must not outlive the test.
_DISCOVERIES: WeakKeyDictionary[object, Discovery] = WeakKeyDictionary()


def _discovery_for(server: object) -> Discovery:
    """The module set `build_app` recorded for `server`; empty for a server built any other way."""
    try:
        return _DISCOVERIES.get(server, Discovery())
    except TypeError:  # not weak-referenceable, e.g. the `None` some tests pass to `lifespan`
        return Discovery()


def _names(names: Sequence[str]) -> str:
    return ",".join(names) if names else "none"


def _log_source_modules(discovery: Discovery) -> None:
    """One INFO line with the resolved module set and the variable as supplied, one WARNING for typos.

    Emitted from `lifespan`, not at import: `setup_logging` runs in `main()` after `app` exists, so
    an import-time line would be dropped. The values are module names and `-`/`*` only.
    """
    active = [loaded.module.name for loaded in discovery.active]
    installed = sorted([*active, *discovery.inactive])
    if installed:
        supplied = f"'{','.join(discovery.requested)}'" if discovery.requested else "(unset)"
        logger.info(
            "Source modules: installed=%s active=%s disabled=%s LAW_MCP_SOURCE_MODULES=%s",
            _names(installed),
            _names(active),
            _names(discovery.inactive),
            supplied,
        )
    if discovery.ignored_unknown:
        logger.warning(
            "Source modules: LAW_MCP_SOURCE_MODULES names modules that are not installed and are switched "
            "off or overridden, ignoring them: %s (installed=%s)",
            ",".join(discovery.ignored_unknown),
            _names(installed),
        )


async def _start_source_modules(
    discovery: Discovery, stack: AsyncExitStack, egress_hub: EgressHub, module_state: dict[str, object]
) -> None:
    """Run each active module's start hook inside `stack`; the first failure aborts startup."""
    for loaded in discovery.active:
        name = loaded.module.name
        host = ModuleHost(egress=egress_hub, caller=loaded.caller_identity(), settings=settings)
        if loaded.module.start is None:
            continue
        try:
            module_state[name] = await stack.enter_async_context(loaded.module.start(host))
        except SourceModuleError:
            raise
        except Exception as error:
            raise SourceModuleError(
                f"Moduł {name} nie wystartował: {type(error).__name__}: {error}. "
                f"Wyłącz moduł, dopisując -{name} na końcu LAW_MCP_SOURCE_MODULES."
            ) from error


@asynccontextmanager
async def lifespan(_server: MCPServer[AppContext]) -> AsyncIterator[AppContext]:
    """Initialize and cleanup server resources."""
    logger.info("Starting Law Scrapper MCP Server v%s", settings.server_version)
    log_pattern_limit_clamping(settings, logger)
    discovery = _discovery_for(_server)
    _log_source_modules(discovery)

    circuit_breaker = CircuitBreaker(
        failure_threshold=settings.circuit_breaker_threshold,
        recovery_timeout=settings.circuit_breaker_recovery_timeout,
        half_open_max_calls=settings.circuit_breaker_half_open_max_calls,
    )
    cache = TTLCache(max_entries=settings.cache_max_entries)
    # Every upstream host the process talks to is registered here, the Sejm included, so a
    # source module can neither take the Sejm's origin nor see its budget. The defaults a
    # module's host starts from are the Sejm's own settings (a module may only lower them).
    egress_hub = EgressHub(
        cache,
        EgressDefaults(
            timeout=settings.api_timeout,
            max_concurrent=settings.api_max_concurrent,
            max_concurrent_content=settings.api_max_concurrent_content,
            rate_per_second=settings.api_rate_per_second,
            rate_burst=settings.api_rate_burst,
            max_server_pause=settings.api_max_server_pause,
            max_attempts=settings.api_max_attempts,
            retry_budget=settings.api_retry_budget,
            user_agent=settings.user_agent,
            failure_threshold=settings.circuit_breaker_threshold,
            recovery_timeout=settings.circuit_breaker_recovery_timeout,
            half_open_max_calls=settings.circuit_breaker_half_open_max_calls,
        ),
    )
    _health_state.set(circuit_breaker, egress_hub)
    client = SejmApiClient(
        cache=cache,
        timeout=settings.api_timeout,
        max_concurrent=settings.api_max_concurrent,
        max_concurrent_content=settings.api_max_concurrent_content,
        rate_per_second=settings.api_rate_per_second,
        rate_burst=settings.api_rate_burst,
        max_server_pause=settings.api_max_server_pause,
        circuit_breaker=circuit_breaker,
        max_attempts=settings.api_max_attempts,
        retry_budget=settings.api_retry_budget,
        user_agent=settings.user_agent,
    )
    egress_hub.adopt(client)
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
    module_state: dict[str, object] = {}

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
        egress=egress_hub,
        module_state=module_state,
    )

    try:
        # Modules start after every core resource exists and stop before any of them closes.
        async with AsyncExitStack() as module_stack:
            await _start_source_modules(discovery, module_stack, egress_hub, module_state)
            yield context
    finally:
        # Cleared first: if `client.close()` raised, a populated handle would
        # let `/health` claim a live breaker after the lifespan had ended.
        _health_state.clear()
        try:
            await client.close()
        finally:
            # Nested so a failed close still empties the cache (#31); the
            # exception itself propagates — swallowing it would hide why the
            # shutdown was unclean. The same holds for the hub: a failing close of
            # a source module's client must not skip `cache.clear()`.
            try:
                await egress_hub.close()
            finally:
                await cache.clear()
        logger.info("Law Scrapper MCP Server stopped")


# Import-time derivation serves the stdio path only: `MCPServer.__init__`
# validates the (auth, token_verifier) pair, and `app.run(transport="stdio")`
# never looks at it again. The HTTP surface has a single derivation point in
# `build_http_app()`, which re-reads the live `settings` (#41) — this pair is
# never the source of truth for what the HTTP app serves.
_auth_settings, _token_verifier = build_auth(settings)


def _register_module_tools(server: MCPServer[AppContext], module: SourceModule) -> None:
    """Register one module's tools and verify it added exactly the tools it declared.

    The SDK ignores a duplicate tool name with only a warning, so a module that shadows a core
    tool, or another module's tool, would otherwise start "successfully" with the wrong tool set.
    """
    before = registered_tool_names(server)
    declared = frozenset(module.tool_names)
    clash = sorted(before & declared)
    if clash:
        raise SourceModuleError(
            f"Moduł {module.name} deklaruje narzędzie, które już istnieje: {', '.join(clash)}. "
            f"Wyłącz moduł, dopisując -{module.name} na końcu LAW_MCP_SOURCE_MODULES."
        )
    module.register_tools(server)
    added = registered_tool_names(server) - before
    if added != declared:
        raise SourceModuleError(
            f"Moduł {module.name} zarejestrował inne narzędzia niż zadeklarował "
            f"(zadeklarowane: {', '.join(sorted(declared)) or 'brak'}; "
            f"zarejestrowane: {', '.join(sorted(added)) or 'brak'}). "
            f"Wyłącz moduł, dopisując -{module.name} na końcu LAW_MCP_SOURCE_MODULES."
        )


def build_app(
    modules: Sequence[LoadedModule] = (),
    *,
    inactive: Sequence[str] = (),
    requested: Sequence[str] = (),
    ignored_unknown: Sequence[str] = (),
) -> MCPServer[AppContext]:
    """Build the server for a given set of active modules.

    Composes the instructions (core text plus module blocks), registers the core tools and then
    each module's, and records the module set for `lifespan`. `inactive`, `requested` and
    `ignored_unknown` only feed the startup log. Production calls this once, below, with the result
    of `discover`; tests build their own servers with fake modules.
    """
    server = MCPServer[AppContext](
        settings.server_name,
        version=settings.server_version,
        instructions=compose_instructions(SERVER_INSTRUCTIONS, CORE_TOOL_COUNT, [item.module for item in modules]),
        lifespan=lifespan,
        auth=_auth_settings,
        token_verifier=_token_verifier,
    )
    register_all_tools(server)
    for loaded in modules:
        _register_module_tools(server, loaded.module)
    _DISCOVERIES[server] = Discovery(
        active=tuple(modules),
        inactive=tuple(inactive),
        requested=tuple(requested),
        ignored_unknown=tuple(ignored_unknown),
    )
    return server


_discovery = discover(settings)
app = build_app(
    _discovery.active,
    inactive=_discovery.inactive,
    requested=_discovery.requested,
    ignored_unknown=_discovery.ignored_unknown,
)


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
            "sources": _health_state.sources(),
        }
    )


def build_http_app() -> ASGIApp:
    """Build the ASGI app served over Streamable HTTP.

    Mirrors `MCPServer.run_streamable_http_async`
    (mcp/server/mcpserver/server.py:1121-1142) minus the uvicorn wiring, which
    this project owns so that `timeout_graceful_shutdown` can be set at all.

    Re-check this function against that SDK method on every `mcp` upgrade: a
    changed `streamable_http_app()` signature would surface here first. Since
    v4.0.0 that includes the auth wiring — `auth` and `token_verifier` are read
    off the server instance (server.py:1305-1306), not passed here.
    """
    # Single derivation point for the HTTP surface (#41): the SDK reads the
    # pair off the instance inside `streamable_http_app()`, so it is derived
    # from the live `settings` here rather than frozen at import time — the
    # same module global every other bootstrap function reads fresh, which is
    # what lets a test swap `settings` and get the auth the server would run.
    # `_token_verifier` is SDK-private; the integration suite already relies
    # on it, so a rename would break there before it broke here.
    app.settings.auth, app._token_verifier = build_auth(settings)
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
        # Whole seconds, validated as an int by `Settings` — no cast here, so
        # a fractional value is refused at startup instead of truncated.
        timeout_graceful_shutdown=settings.shutdown_grace,
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


def main() -> None:
    """Entry point for the server."""
    setup_logging(settings.log_level, settings.log_format)
    log_remote_bind_warning(settings, logger)
    if settings.transport == "streamable-http":
        run_streamable_http()
    else:
        app.run(transport="stdio")


if __name__ == "__main__":
    main()
