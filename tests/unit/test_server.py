"""Tests for MCP server registration and lifespan."""

from __future__ import annotations

from fastmcp import Client

from law_scrapper_mcp.server import app, lifespan

EXPECTED_TOOLS = sorted(
    [
        "analyze_act_relationships",
        "browse_acts",
        "calculate_legal_date",
        "compare_acts",
        "filter_results",
        "get_act_details",
        "get_system_metadata",
        "list_loaded_documents",
        "list_result_sets",
        "read_act_content",
        "search_in_act",
        "search_legal_acts",
        "track_legal_changes",
    ]
)

LIFESPAN_KEYS = {
    "client",
    "cache",
    "document_store",
    "content_processor",
    "result_store",
    "metadata_service",
    "search_service",
    "act_service",
    "changes_service",
}


async def test_all_tools_registered() -> None:
    """list_tools() returns exactly 13 tools."""
    async with Client(app) as client:
        tools = await client.list_tools()
        tool_names = sorted(tool.name for tool in tools)

    assert len(tool_names) == 13
    assert tool_names == EXPECTED_TOOLS


async def test_tool_names_match_expected() -> None:
    """Tool names match the MCP contract."""
    async with Client(app) as client:
        tools = await client.list_tools()
        tool_names = {tool.name for tool in tools}

    assert tool_names == set(EXPECTED_TOOLS)


async def test_lifespan_yields_services() -> None:
    """Lifespan initializes all required service keys."""
    async with lifespan(app) as ctx:
        assert set(ctx.keys()) == LIFESPAN_KEYS
        assert ctx["metadata_service"] is not None
        assert ctx["search_service"] is not None
        assert ctx["act_service"] is not None
