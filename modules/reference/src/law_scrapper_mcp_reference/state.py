"""Runtime state of the reference module and its typed accessor."""

from __future__ import annotations

from dataclasses import dataclass

from law_scrapper_mcp.context import AppContext

MODULE_NAME = "reference"


@dataclass(frozen=True, slots=True)
class ReferenceState:
    """What the start hook leaves in `AppContext.module_state` for the tools to read."""

    module: str
    version: str
    started: bool = True


def reference_state(app_context: AppContext) -> ReferenceState:
    """Return this module's state; the core stores it under the module name.

    Raises:
        RuntimeError: The module's tool ran although its start hook did not (a wiring bug).
    """
    state = app_context.module_state.get(MODULE_NAME)
    if not isinstance(state, ReferenceState):
        raise RuntimeError("Moduł reference nie został uruchomiony: brak jego stanu w kontekście aplikacji.")
    return state
