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
from law_scrapper_mcp_saos.state import MODULE_NAME, SaosState, saos_host
from law_scrapper_mcp_saos.tools import register_all_tools

__all__ = ["module"]


@asynccontextmanager
async def start(host: ModuleHost) -> AsyncIterator[SaosState]:
    """Register the SAOS host and leave the client for the tools; the hub closes it at shutdown."""
    egress = host.egress.client_for(saos_host(host.settings), host.caller)
    yield SaosState(
        client=SaosClient(egress, search_ttl=host.settings.cache_search_ttl),
        module=host.caller.module,
        version=host.caller.version,
    )


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
        "i rodzaj wydziału (bez treści i bez danych osobowych). Odnośniki dla pierwszych 5 wyników wyszukiwania "
        "pobierze też parametr include_portal_links=true.\n"
        "Wyrok i jego uzasadnienie to dwa osobne rekordy tej samej sprawy. Wydziały pracy sądów powszechnych "
        'rozpoznasz po kodzie wydziału: filter_results z pattern="(15|18|21)$" i field="division_code". '
        "Fraza i sygnatura są wysyłane do serwisu saos.org.pl; awaria SAOS nie wpływa na inne źródła."
    ),
    register_tools=register_all_tools,
    start=start,
)
