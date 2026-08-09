"""Testy opisów narzędzi widocznych dla klienta MCP (klaster 1, D7)."""

from __future__ import annotations

import json
from typing import Any


async def _get_tool(mcp_client, name: str):
    tools = await mcp_client.list_tools()
    return next(tool for tool in tools if tool.name == name)


def _parse_tool_result(result: Any) -> dict[str, Any]:
    """Wydobądź payload JSON z wyniku `call_tool` FastMCP."""
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
        """Kryterium 3 na poziomie protokołu: klient dostaje komunikat, nie ciszę.

        Zestaw wyników musi realnie istnieć (utworzony przez search_legal_acts),
        bo `services/result_store.py` waliduje `result_set_id` przed kompilacją
        wzorca — z nieistniejącym zestawem `compile_pattern` nigdy nie jest wołane
        i test przechodzi wyłącznie gałęzią "zestaw nie istnieje", bez sprawdzenia
        ścieżki, którą deklaruje (walidacja składni RE2 w PatternValidationError).
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
