"""Runtime state of the voivodeship-journals module and its typed accessor."""

from __future__ import annotations

from dataclasses import dataclass

from law_scrapper_mcp.context import AppContext

MODULE_NAME = "voivodeship-journals"


@dataclass(frozen=True, slots=True)
class JournalsState:
    """What the start hook leaves in `AppContext.module_state`.

    Attributes:
        codes: Publisher codes whose journal host has a client, i.e. the codes the module serves.
    """

    codes: tuple[str, ...]


def journals_state(app_context: AppContext) -> JournalsState:
    """Return this module's state; the core stores it under the module name.

    Raises:
        RuntimeError: The module's code ran although its start hook did not (a wiring bug).
    """
    state = app_context.module_state.get(MODULE_NAME)
    if not isinstance(state, JournalsState):
        raise RuntimeError("Moduł voivodeship-journals nie został uruchomiony: brak jego stanu w kontekście aplikacji.")
    return state
