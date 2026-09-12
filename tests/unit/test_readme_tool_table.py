"""A10: the README tool-properties table stays in sync with tools/list.

The table is the one place an integrator learns which tools reach
api.sejm.gov.pl without calling tools/list, so a drifted row would mislead
exactly the reader it exists for.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from mcp import Client

from law_scrapper_mcp.server import app

pytestmark = pytest.mark.asyncio

README = Path(__file__).parents[2] / "README.md"
ROW = re.compile(
    r"^\| `(?P<name>[a-z_]+)` \| (?P<title>[^|]+?) \| (?P<read_only>true|false) \| (?P<open_world>true|false) \|$"
)


def _rows() -> dict[str, re.Match[str]]:
    rows: dict[str, re.Match[str]] = {}
    for line in README.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line)
        if match:
            rows[match["name"]] = match
    return rows


async def test_readme_tool_table_matches_tools_list() -> None:
    rows = _rows()
    async with Client(app) as client:
        tools = (await client.list_tools()).tools

    assert set(rows) == {tool.name for tool in tools}
    for tool in tools:
        row = rows[tool.name]
        assert tool.annotations is not None
        assert row["title"] == tool.title, tool.name
        assert row["read_only"] == str(tool.annotations.read_only_hint).lower(), tool.name
        assert row["open_world"] == str(tool.annotations.open_world_hint).lower(), tool.name
        assert tool.annotations.destructive_hint is False, tool.name
        assert tool.annotations.idempotent_hint is True, tool.name
