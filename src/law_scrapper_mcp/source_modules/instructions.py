"""Compose the server instructions from the core text and the active modules."""

from __future__ import annotations

from collections.abc import Sequence

from law_scrapper_mcp.source_modules.contract import SourceModule


def compose_instructions(core_text: str, core_tool_count: int, modules: Sequence[SourceModule]) -> str:
    """Return the instructions an agent reads at `initialize` and `server/discover`.

    With no module the result is `core_text` itself, byte for byte. With modules the tool count in
    the core header becomes core plus module tools, and one block per module is appended.

    Raises:
        ValueError: The core header does not contain `DOSTĘPNE NARZĘDZIA (<core_tool_count>):`
            exactly once, so the count could not be updated.
    """
    if not modules:
        return core_text
    header = f"DOSTĘPNE NARZĘDZIA ({core_tool_count}):"
    if core_text.count(header) != 1:
        raise ValueError(f"Core instructions must contain {header!r} exactly once to count module tools.")
    total = core_tool_count + sum(len(module.tool_names) for module in modules)
    blocks = [
        f"MODUŁ ŹRÓDŁA — {module.name} (narzędzi: {len(module.tool_names)})\n{module.instructions.strip()}"
        for module in modules
    ]
    return core_text.replace(header, f"DOSTĘPNE NARZĘDZIA ({total}):") + "\n\n" + "\n\n".join(blocks)
