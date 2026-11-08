"""Runtime state of the SAOS module and its typed accessor."""

from __future__ import annotations

from dataclasses import dataclass

from law_scrapper_mcp.client.egress import EgressHost
from law_scrapper_mcp.config import Settings
from law_scrapper_mcp.context import AppContext
from law_scrapper_mcp.services.coverage import CoverageRegistry
from law_scrapper_mcp_saos.client import SaosClient

MODULE_NAME = "saos"
HOST_LABEL = "SAOS"
BASE_URL = "https://www.saos.org.pl/api"

# SAOS is a public academic service, so this module asks for less than the hub's default of 5 requests
# per second with a burst of 10 (the hub allows a module to lower the pace, never to raise it): a page
# of 100 records is about 140 kB and the first request of a session took 9 s. A search with portal links
# is six requests, which at this pace takes about one second after the burst.
RATE_PER_SECOND = 2.0
RATE_BURST = 4


def saos_host(settings: Settings) -> EgressHost:
    """The SAOS host description: the module's pace, or the operator's lower one for the Sejm.

    The hub refuses a host that asks for more than its defaults, and the defaults are the operator's
    `LAW_MCP_API_RATE_*` settings. Taking the lower of the two keeps a server whose operator slowed the
    Sejm down from failing to start because of this module.
    """
    return EgressHost(
        name=HOST_LABEL,
        base_url=BASE_URL,
        rate_per_second=min(RATE_PER_SECOND, settings.api_rate_per_second),
        rate_burst=min(RATE_BURST, settings.api_rate_burst),
    )


@dataclass(frozen=True, slots=True)
class SaosState:
    """What the start hook leaves in `AppContext.module_state` for the tools to read."""

    client: SaosClient
    module: str
    version: str
    # The core's coverage registry, which holds this module's provider under `coverage.COVERAGE_KEY`; the
    # search service reads coverage through it (memo, lock and fallback included). `None` outside the start hook.
    coverage: CoverageRegistry | None = None


def saos_state(app_context: AppContext) -> SaosState:
    """Return this module's state; the core stores it under the module name.

    Raises:
        RuntimeError: A SAOS tool ran although the start hook did not (a wiring bug).
    """
    state = app_context.module_state.get(MODULE_NAME)
    if not isinstance(state, SaosState):
        raise RuntimeError("Moduł saos nie został uruchomiony: brak jego stanu w kontekście aplikacji.")
    return state
