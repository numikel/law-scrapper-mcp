"""Source modules: optional packages that add tools for further legal sources.

The core discovers installed modules through the `law_scrapper_mcp.source_modules` entry-point
group, activates them from `LAW_MCP_SOURCE_MODULES` and counts their tools next to its own.
This package is a root-level helper beside `server.py`, not a layer: it imports nothing from
`tools/`, and nothing in the core imports a module.

Submodules are imported explicitly (`contract`, `discovery`, `instructions`); this file stays
empty of imports so that `config.py` can never be dragged into an import cycle through it.
"""
