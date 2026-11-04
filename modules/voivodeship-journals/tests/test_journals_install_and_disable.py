"""Install and disable proof for voivodeship-journals, against the real installed distribution.

STDIO cases start a real `python -m law_scrapper_mcp` and talk MCP to it; nothing is faked. They never
call a journal tool: a subprocess cannot be mocked, and these tests must not reach the journal hosts.
`tools/list` is compared with the same configuration with this module switched off, so a second
installed module, with or without tools, cannot change what is expected.

Run with the member installed: `uv sync --extra dev --all-packages`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

PROJECT_ROOT = Path(__file__).parents[3]
VARIABLE = "LAW_MCP_SOURCE_MODULES"
MODULE = "voivodeship-journals"
MODULE_IMPORT = "law_scrapper_mcp_voivodeship_journals"
BLOCK = f"MODUŁ ŹRÓDŁA — {MODULE} (narzędzi: 0)"
CORE_TOOL_COUNT = 13


def _environment(value: str | None) -> dict[str, str]:
    """A clean child environment: the variable is either exactly `value` or absent."""
    environment = {key: item for key, item in os.environ.items() if key != VARIABLE}
    if value is not None:
        environment[VARIABLE] = value
    return environment


async def _stdio(value: str | None) -> tuple[list[str], str]:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "law_scrapper_mcp"],
        cwd=str(PROJECT_ROOT),
        env={**get_default_environment(), **_environment(value), "LAW_MCP_TRANSPORT": "stdio"},
    )
    with open(os.devnull, "w", encoding="utf-8") as errlog:
        async with Client(stdio_client(parameters, errlog=errlog)) as client:
            tools = (await client.list_tools()).tools
            instructions = client.instructions
    assert instructions is not None
    return sorted(tool.name for tool in tools), instructions


def _with_module_off(value: str | None) -> str:
    return f"{value},-{MODULE}" if value else f"-{MODULE}"


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    "value",
    [
        pytest.param(None, id="unset: every installed module is active"),
        pytest.param(f"-*,{MODULE}", id="-*,voivodeship-journals runs only this module"),
        pytest.param(MODULE, id="a bare name leaves everything on"),
        pytest.param("-reference", id="another module off, this one on"),
    ],
)
async def test_the_module_is_active_adds_a_block_and_no_tool(value: str | None) -> None:
    names, instructions = await _stdio(value)
    names_without_module, _ = await _stdio(_with_module_off(value))

    assert BLOCK in instructions
    assert names == names_without_module, "the module adds no tool"
    assert f"DOSTĘPNE NARZĘDZIA ({len(names)}):" in instructions
    assert "POL_WOJ_PM" in instructions and "POL_WOJ_SK" in instructions


@pytest.mark.integration
@pytest.mark.anyio
async def test_only_this_module_gives_exactly_the_core_tool_set() -> None:
    names, _ = await _stdio(f"-*,{MODULE}")
    core_names, _ = await _stdio("-*")

    assert names == core_names
    assert len(names) == CORE_TOOL_COUNT


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    "value",
    [
        pytest.param(f"-{MODULE}", id="-voivodeship-journals"),
        pytest.param("-*", id="-*"),
        pytest.param(f"{MODULE},-*", id="voivodeship-journals,-*"),
    ],
)
async def test_a_switched_off_module_leaves_no_block(value: str) -> None:
    _, instructions = await _stdio(value)

    assert BLOCK not in instructions
    assert "POL_WOJ" not in instructions


@pytest.mark.integration
@pytest.mark.anyio
async def test_with_every_module_off_the_instructions_equal_the_core_text() -> None:
    from law_scrapper_mcp.server import SERVER_INSTRUCTIONS

    _, instructions = await _stdio("-*")

    assert instructions == SERVER_INSTRUCTIONS


def _package_imported_after_import_of_server(value: str | None) -> bool:
    """Import the server in a child process and report whether the module's package was imported."""
    probe = (
        "import sys\n"
        "import law_scrapper_mcp.server\n"
        f"print('imported' if {MODULE_IMPORT!r} in sys.modules else 'absent')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=PROJECT_ROOT,
        env=_environment(value),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return result.stdout.strip().splitlines()[-1] == "imported"


@pytest.mark.parametrize(
    ("value", "imported"),
    [
        pytest.param(f"-{MODULE}", False, id="-voivodeship-journals"),
        pytest.param("-*", False, id="-*"),
        pytest.param(f"{MODULE},-*", False, id="voivodeship-journals,-*"),
        pytest.param(None, True, id="unset"),
        pytest.param(f"-*,{MODULE}", True, id="-*,voivodeship-journals"),
    ],
)
def test_a_switched_off_module_is_never_imported(value: str | None, imported: bool) -> None:
    assert _package_imported_after_import_of_server(value) is imported
