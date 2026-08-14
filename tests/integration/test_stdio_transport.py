"""Contract tests for the real STDIO transport."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

pytestmark = [pytest.mark.integration, pytest.mark.anyio]
PROJECT_ROOT = Path(__file__).parents[2]
PROTOCOL_FLOOR = date(2026, 7, 28)
APPLICATION_LOG_MARKERS = (
    "Starting Law Scrapper MCP Server",
    " - INFO - ",
    " - ERROR - ",
    " - WARNING - ",
    " - DEBUG - ",
)


def _read_stderr_log(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8", "cp1250"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _assert_stdout_lines_are_json_rpc_only(stdout_lines: list[str]) -> None:
    assert stdout_lines, "Expected MCP server stdout traffic during the session"
    for line in stdout_lines:
        for marker in APPLICATION_LOG_MARKERS:
            assert marker not in line, f"application log leaked to stdout: {line!r}"
        payload = json.loads(line)
        assert payload.get("jsonrpc") == "2.0"


async def test_stdio_discovery_tools_success_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import mcp.client.stdio as stdio_module

    captured_stdout: list[str] = []
    original_parse_line = stdio_module._parse_line

    def capture_parse_line(line: str):
        stripped = line.strip()
        if stripped:
            captured_stdout.append(stripped)
        return original_parse_line(line)

    monkeypatch.setattr(stdio_module, "_parse_line", capture_parse_line)

    stderr_fd, stderr_path = tempfile.mkstemp(suffix=".log")
    stderr_file = os.fdopen(stderr_fd, "w", encoding="utf-8")
    try:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "law_scrapper_mcp"],
            cwd=str(PROJECT_ROOT),
            env={
                **get_default_environment(),
                "LAW_MCP_TRANSPORT": "stdio",
                "LAW_MCP_LOG_LEVEL": "INFO",
            },
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
        stderr_content = _read_stderr_log(Path(stderr_path))
    finally:
        if not stderr_file.closed:
            stderr_file.close()
        Path(stderr_path).unlink(missing_ok=True)

    assert len(tools) == 13
    assert success.is_error is False
    assert success.structured_content["data"]["calculated_date"] == "2026-01-02"
    assert failure.is_error is True
    assert "Starting Law Scrapper MCP Server" in stderr_content
    _assert_stdout_lines_are_json_rpc_only(captured_stdout)
