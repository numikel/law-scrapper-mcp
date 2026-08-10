"""Contract tests for the real STDIO transport."""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = [pytest.mark.integration, pytest.mark.anyio]
PROJECT_ROOT = Path(__file__).parents[2]
PROTOCOL_FLOOR = date(2026, 7, 28)


async def test_stdio_discovery_tools_success_and_error() -> None:
    stderr_fd, stderr_path = tempfile.mkstemp(suffix=".log")
    stderr_file = os.fdopen(stderr_fd, "w", encoding="utf-8")
    try:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "law_scrapper_mcp"],
            cwd=str(PROJECT_ROOT),
            env={**os.environ, "LAW_MCP_LOG_LEVEL": "INFO"},
        )

        async with Client(stdio_client(parameters, errlog=stderr_file)) as client:
            assert date.fromisoformat(client.protocol_version) >= PROTOCOL_FLOOR
            tools = (await client.list_tools()).tools
            success = await client.call_tool(
                "calculate_legal_date",
                {"days": 1, "base_date": "2026-01-01"},
            )
            failure = await client.call_tool("get_act_details", {"eli": "INVALID"})

        stderr_file.flush()
        stderr_file.close()
        stderr_content = Path(stderr_path).read_text(encoding="utf-8")
    finally:
        if not stderr_file.closed:
            stderr_file.close()
        Path(stderr_path).unlink(missing_ok=True)

    assert len(tools) == 13
    assert success.is_error is False
    assert success.structured_content["data"]["calculated_date"] == "2026-01-02"
    assert failure.is_error is True
    assert "Starting Law Scrapper MCP Server" in stderr_content
