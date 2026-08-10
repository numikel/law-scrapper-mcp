"""Shared helpers for MCP client integration tests."""

from __future__ import annotations

from typing import Any


def parse_tool_result(result: Any) -> dict[str, Any]:
    """Extract structured content from an official MCP call_tool result."""
    assert result.is_error is False
    assert result.structured_content is not None
    return result.structured_content
