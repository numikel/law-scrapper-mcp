"""Tool registration for Law Scrapper MCP."""

from fastmcp import FastMCP


def register_all_tools(mcp: FastMCP) -> None:
    """Register all tools with the MCP server."""
    from law_scrapper_mcp.tools.act_content import register as reg_act_content
    from law_scrapper_mcp.tools.act_details import register as reg_act_details
    from law_scrapper_mcp.tools.act_search import register as reg_act_search
    from law_scrapper_mcp.tools.browse import register as reg_browse
    from law_scrapper_mcp.tools.changes import register as reg_changes
    from law_scrapper_mcp.tools.compare import register as reg_compare
    from law_scrapper_mcp.tools.dates import register as reg_dates
    from law_scrapper_mcp.tools.filter_results import register as reg_filter
    from law_scrapper_mcp.tools.metadata import register as reg_metadata
    from law_scrapper_mcp.tools.relationships import register as reg_relationships
    from law_scrapper_mcp.tools.search import register as reg_search

    reg_metadata(mcp)
    reg_search(mcp)
    reg_browse(mcp)
    reg_act_details(mcp)
    reg_act_content(mcp)
    reg_act_search(mcp)
    reg_relationships(mcp)
    reg_changes(mcp)
    reg_dates(mcp)
    reg_filter(mcp)
    reg_compare(mcp)
