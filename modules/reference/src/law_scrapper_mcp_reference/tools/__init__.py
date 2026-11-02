"""Tools of the reference module; the layout mirrors the core's `tools/` package."""

from mcp.server import MCPServer

from law_scrapper_mcp.context import AppContext


def register_all_tools(mcp: MCPServer[AppContext]) -> None:
    """Register every tool of the module."""
    from law_scrapper_mcp_reference.tools.ping import register as reg_ping

    reg_ping(mcp)
