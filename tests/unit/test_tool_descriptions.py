"""Tests for MCP-visible tool descriptions"""

from __future__ import annotations

import json
from typing import Any


async def _get_tool(mcp_client, name: str):
    tools = await mcp_client.list_tools()
    return next(tool for tool in tools if tool.name == name)


def _parse_tool_result(result: Any) -> dict[str, Any]:
    """Extract the JSON payload from a FastMCP `call_tool` result."""
    if hasattr(result, "data") and result.data is not None:
        if isinstance(result.data, dict):
            return result.data
        if isinstance(result.data, str):
            return json.loads(result.data)

    if result.content:
        return json.loads(result.content[0].text)

    raise ValueError(f"Unexpected tool result format: {result!r}")


class TestFilterResultsDescriptions:
    async def test_pattern_description_names_supported_subset(self, mcp_client) -> None:
        tool = await _get_tool(mcp_client, "filter_results")
        description = tool.inputSchema["properties"]["pattern"]["description"]

        assert "lookaround" in description.lower()
        assert "backrefer" in description.lower()
        assert r"\p{L}" in description

    async def test_tool_description_mentions_record_limit(self, mcp_client) -> None:
        tool = await _get_tool(mcp_client, "filter_results")

        assert "100" in (tool.description or "")

    async def test_unsupported_pattern_surfaces_polish_error(self, mcp_client) -> None:
        """Criterion 3 at the protocol level: the client gets a message, not silence.

        The result set must actually exist (created via search_legal_acts),
        because `services/result_store.py` validates `result_set_id` before
        compiling the pattern — with a missing set, `compile_pattern` is never
        called and the test would pass only via the "set not found" branch,
        without exercising the path it claims to cover (RE2 syntax validation
        in PatternValidationError).
        """
        search = await mcp_client.call_tool("search_legal_acts", {"year": 2024})
        search_payload = _parse_tool_result(search)
        result_set_id = search_payload["data"]["result_set_id"]

        result = await mcp_client.call_tool(
            "filter_results",
            {"result_set_id": result_set_id, "pattern": "(?<=Ustawa)o"},
        )

        raw = str(result)
        assert "nie jest obsługiwany" in raw
