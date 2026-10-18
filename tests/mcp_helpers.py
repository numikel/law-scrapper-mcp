"""Shared helpers for MCP client tests, unit and integration alike."""

from __future__ import annotations

from typing import Any

# Written out by hand: the one place the expected tool set lives. Every tool-count
# assertion derives from it, so adding or removing a tool means editing this list only.
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


def parse_tool_result(result: Any) -> dict[str, Any]:
    """Extract structured content from an official MCP call_tool result."""
    assert result.is_error is False
    assert result.structured_content is not None
    return result.structured_content
