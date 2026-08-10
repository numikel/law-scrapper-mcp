"""Tool-boundary tests for calculate_legal_date."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio


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

    assert result.is_error is True
    assert result.structured_content is None
    assert "Nieprawidłowa wartość przesunięcia" in result.content[0].text
