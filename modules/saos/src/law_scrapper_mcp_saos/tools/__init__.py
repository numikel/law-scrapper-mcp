"""Tools of the SAOS module; the layout mirrors the core's `tools/` package."""

from mcp.server import MCPServer

from law_scrapper_mcp.context import AppContext


def register_all_tools(mcp: MCPServer[AppContext]) -> None:
    """Register every tool of the module."""
    from law_scrapper_mcp_saos.tools.search_saos_judgments import register as reg_search

    reg_search(mcp)
