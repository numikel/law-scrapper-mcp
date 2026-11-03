"""Runtime state of the SAOS module and its typed accessor."""

from __future__ import annotations

from dataclasses import dataclass

from law_scrapper_mcp.context import AppContext
from law_scrapper_mcp_saos.client import SaosClient

MODULE_NAME = "saos"
HOST_LABEL = "SAOS"
BASE_URL = "https://www.saos.org.pl/api"


@dataclass(frozen=True, slots=True)
class SaosState:
    """What the start hook leaves in `AppContext.module_state` for the tools to read."""

    client: SaosClient
    module: str
    version: str


def saos_state(app_context: AppContext) -> SaosState:
    """Return this module's state; the core stores it under the module name.

    Raises:
        RuntimeError: A SAOS tool ran although the start hook did not (a wiring bug).
    """
    state = app_context.module_state.get(MODULE_NAME)
    if not isinstance(state, SaosState):
        raise RuntimeError("Moduł saos nie został uruchomiony: brak jego stanu w kontekście aplikacji.")
    return state
