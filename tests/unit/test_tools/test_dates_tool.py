"""Tool-boundary tests for calculate_legal_date."""

from __future__ import annotations

import json

import pytest


@pytest.mark.parametrize("offset_param", ["days", "months", "years"])
async def test_calculate_legal_date_rejects_boolean_offsets_at_tool_boundary(
    mcp_client,
    offset_param: str,
) -> None:
    """Boolean offsets must reach DateService validation and return a tool error."""
    result = await mcp_client.call_tool(
        "calculate_legal_date",
        {offset_param: True, "base_date": "2024-01-01"},
    )
    payload = json.loads(result.content[0].text)

    assert payload.get("error") is not None
    assert "Nieprawidłowa wartość przesunięcia" in payload["error"]
