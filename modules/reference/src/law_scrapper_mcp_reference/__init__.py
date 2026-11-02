"""Reference source module: the smallest module that exercises the whole contract.

It has a descriptor (`module`), one tool, a start hook that leaves typed state in
`AppContext.module_state`, and an accessor for that state. Copy it to start a new module.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from law_scrapper_mcp.source_modules.contract import ModuleHost, SourceModule
from law_scrapper_mcp_reference.state import MODULE_NAME, ReferenceState
from law_scrapper_mcp_reference.tools import register_all_tools

__all__ = ["module"]


@asynccontextmanager
async def start(host: ModuleHost) -> AsyncIterator[ReferenceState]:
    """Leave the module's state for the tools. No egress: this module has no upstream host."""
    yield ReferenceState(module=host.caller.module, version=host.caller.version)


module = SourceModule(
    name=MODULE_NAME,
    tool_names=("reference_ping",),
    instructions=(
        "Moduł referencyjny (tylko demonstracja mechanizmu modułów źródeł):\n"
        "- reference_ping — zwraca nazwę i wersję modułu oraz potwierdza, że moduł wystartował.\n"
        "Moduł nie wywołuje żadnego zewnętrznego źródła i nie służy do pracy z aktami prawnymi."
    ),
    register_tools=register_all_tools,
    start=start,
)
