"""SAOS source module: Polish court judgments from www.saos.org.pl.

The descriptor is `module`; the start hook registers the SAOS host with the core's egress hub (which
gives it its own rate limit, circuit breaker, cache namespace and `/health` entry) and leaves the
client in `AppContext.module_state` for the tools.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from law_scrapper_mcp.source_modules.contract import ModuleHost, SourceModule
from law_scrapper_mcp_saos.client import SaosClient
from law_scrapper_mcp_saos.coverage import COVERAGE_KEY, SaosCoverageProvider
from law_scrapper_mcp_saos.state import MODULE_NAME, SaosState, saos_host
from law_scrapper_mcp_saos.tools import register_all_tools

__all__ = ["module"]


@asynccontextmanager
async def start(host: ModuleHost) -> AsyncIterator[SaosState]:
    """Register the SAOS host and its coverage provider, and leave the client for the tools.

    The hub closes the client at shutdown; the provider is unregistered when the hook exits, before the
    core closes its resources. No request is sent at start: coverage is measured on the first question.
    """
    egress = host.egress.client_for(saos_host(host.settings), host.caller)
    client = SaosClient(
        egress,
        search_ttl=host.settings.cache_search_ttl,
        detail_max_bytes=host.settings.doc_store_max_size_bytes,
    )
    host.coverage.register(SaosCoverageProvider(client))
    try:
        yield SaosState(
            client=client,
            module=host.caller.module,
            version=host.caller.version,
            coverage=host.coverage,
        )
    finally:
        host.coverage.unregister(COVERAGE_KEY)


module = SourceModule(
    name=MODULE_NAME,
    tool_names=("search_saos_judgments", "get_saos_judgment_details"),
    instructions=(
        "Moduł SAOS (orzeczenia sądów i trybunałów z serwisu saos.org.pl):\n"
        "- search_saos_judgments — wyszukuje orzeczenia po frazie, sygnaturze, dacie, rodzaju sądu i rodzaju "
        "orzeczenia (od najnowszych, dowolny offset). Wynik trafia do zestawu result_set_id, który zawęzisz "
        "narzędziem filter_results (pola wzorca: signature, court, division, division_code, type, excerpt; "
        "zakres dat: date_field=judgment_date).\n"
        "- get_saos_judgment_details — jedno żądanie do SAOS o szczegóły orzeczenia: odnośnik do portalu sądu "
        "i rodzaj wydziału. Odnośniki dla pierwszych 5 wyników wyszukiwania pobierze też parametr "
        "include_portal_links=true. Z load_content=true wczytuje treść orzeczenia (najwyżej 3 wywołania do SAOS) "
        "jako dokument 'SAOS/<source_id>', który czytasz read_act_content(eli='SAOS/<source_id>') i przeszukujesz "
        "search_in_act; pole loaded.reasoning mówi, gdzie jest uzasadnienie, a 'Brak uzasadnienia w źródle' "
        "oznacza, że SAOS go nie ma (tylko to pole jest wiarygodne: nagłówek w samym tekście orzeczenia niczego "
        "nie dowodzi). Tekst może zawierać dane osobowe pozostawione przez sąd.\n"
        "Wyrok i jego uzasadnienie to dwa osobne rekordy tej samej sprawy; load_content=true dołącza uzasadnienie "
        "automatycznie, gdy jest dokładnie jedno. Wydziały pracy sądów powszechnych "
        'rozpoznasz po kodzie wydziału: filter_results z pattern="(15|18|21)$" i field="division_code". '
        "Gdy chcesz tylko orzeczenia pracy, użyj search_saos_judgments z labour_only=true: serwer skanuje do 5 stron "
        "SAOS (do 5 żądań) i zwraca kursor scan.next_offset zamiast total_count; dla Sądu Najwyższego "
        "(court_type=SUPREME) wybiera Izbę Pracy, a dla pozostałych rodzajów sądów odmawia. "
        "Fraza i sygnatura są wysyłane do serwisu saos.org.pl; awaria SAOS nie wpływa na inne źródła.\n"
        "- Zasięg SAOS (najnowsze orzeczenie i liczba orzeczeń dla każdego rodzaju sądu) podaje "
        "get_system_metadata(category='coverage'); wskazówka o luce pojawia się tylko wtedy, gdy zapytanie "
        "o rodzaj sądu sięga poza zasięg źródła."
    ),
    register_tools=register_all_tools,
    start=start,
)
