"""Tests for MCP-visible tool descriptions"""

from __future__ import annotations

import pytest

from mcp_helpers import parse_tool_result

pytestmark = pytest.mark.anyio


async def _get_tool(mcp_client, name: str):
    tools = (await mcp_client.list_tools()).tools
    return next(tool for tool in tools if tool.name == name)


class TestFilterResultsDescriptions:
    async def test_pattern_description_names_supported_subset(self, mcp_client) -> None:
        tool = await _get_tool(mcp_client, "filter_results")
        description = tool.input_schema["properties"]["pattern"]["description"]

        assert "lookaround" in description.lower()
        assert "backrefer" in description.lower()
        assert r"\p{L}" in description

    async def test_tool_description_mentions_record_limit(self, mcp_client) -> None:
        tool = await _get_tool(mcp_client, "filter_results")

        assert "100" in (tool.description or "")

    async def test_unsupported_pattern_surfaces_polish_error(self, mcp_client) -> None:
        search = await mcp_client.call_tool("search_legal_acts", {"year": 2024})
        search_payload = parse_tool_result(search)
        result_set_id = search_payload["data"]["result_set_id"]

        result = await mcp_client.call_tool(
            "filter_results",
            {"result_set_id": result_set_id, "pattern": "(?<=Ustawa)o"},
        )

        assert result.is_error is True
        assert result.structured_content is None
        assert "nie jest obsługiwany" in result.content[0].text
