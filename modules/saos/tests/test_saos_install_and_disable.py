"""Install and disable proof for the SAOS module, against the real installed distribution.

Nothing here is a fake: the entry point comes from the installed distribution metadata, and the STDIO cases
start a real `python -m law_scrapper_mcp` and talk MCP to it. They never call a SAOS tool: a subprocess cannot
be mocked, and these tests must not reach SAOS. Every count is derived from the same configuration with this
module switched off, so another installed module, with or without tools, cannot change what is expected.

Run with the member installed: `uv sync --extra dev --all-packages`.
"""

from __future__ import annotations

import importlib.metadata
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

from law_scrapper_mcp.config import Settings
from law_scrapper_mcp.source_modules.contract import ENTRY_POINT_GROUP, SourceModule
from law_scrapper_mcp.source_modules.discovery import discover

PROJECT_ROOT = Path(__file__).parents[3]
VARIABLE = "LAW_MCP_SOURCE_MODULES"
MODULE = "saos"
MODULE_IMPORT = "law_scrapper_mcp_saos"
TOOLS = {"search_saos_judgments", "get_saos_judgment_details"}
BLOCK = f"MODUŁ ŹRÓDŁA — {MODULE} (narzędzi: {len(TOOLS)})"
CORE_TOOL_COUNT = 13


async def _in_process_tool_names(tokens: list[str]) -> list[str]:
    """Tool names of a server built in-process for the given `LAW_MCP_SOURCE_MODULES` tokens."""
    from law_scrapper_mcp.server import build_app

    discovery = discover(Settings(source_modules=tokens))
    server = build_app(discovery.active, inactive=discovery.inactive)
    return sorted(tool.name for tool in await server.list_tools())


def _entry_points() -> list[importlib.metadata.EntryPoint]:
    return [ep for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP) if ep.name == MODULE]


def _environment(value: str | None) -> dict[str, str]:
    """A clean child environment: the variable is either exactly `value` or absent."""
    environment = {key: item for key, item in os.environ.items() if key != VARIABLE}
    if value is not None:
        environment[VARIABLE] = value
    return environment


def _with_module_off(value: str | None) -> str:
    return f"{value},-{MODULE}" if value else f"-{MODULE}"


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


# --- the entry point and in-process discovery ---------------------------------------------------


def test_the_entry_point_is_installed_in_the_module_group() -> None:
    found = _entry_points()

    assert len(found) == 1, "the member is not installed: run `uv sync --extra dev --all-packages`"
    assert found[0].value == "law_scrapper_mcp_saos:module"
    assert found[0].dist is not None
    assert found[0].dist.name == "law-scrapper-mcp-saos"
    assert found[0].dist.version == "0.1.0"


def test_the_descriptor_name_equals_the_entry_point_name_and_declares_both_tools() -> None:
    (entry_point,) = _entry_points()

    descriptor = entry_point.load()

    assert isinstance(descriptor, SourceModule)
    assert descriptor.name == entry_point.name == MODULE
    assert set(descriptor.tool_names) == TOOLS
    assert descriptor.start is not None


@pytest.mark.asyncio
async def test_real_discovery_adds_exactly_the_two_tools_to_what_the_server_has_without_the_module() -> None:
    from law_scrapper_mcp.server import build_app

    discovery = discover(Settings(source_modules=[]))
    server = build_app(discovery.active, inactive=discovery.inactive)

    names = sorted(tool.name for tool in await server.list_tools())
    without = await _in_process_tool_names([f"-{MODULE}"])
    assert set(names) - set(without) == TOOLS
    assert len(names) == len(without) + len(TOOLS)
    assert server.instructions is not None
    assert f"DOSTĘPNE NARZĘDZIA ({len(names)}):" in server.instructions
    assert BLOCK in server.instructions


@pytest.mark.asyncio
async def test_real_discovery_with_every_module_switched_off_is_the_core() -> None:
    discovery = discover(Settings(source_modules=["-*"]))

    names = await _in_process_tool_names(["-*"])

    assert len(names) == CORE_TOOL_COUNT
    assert not TOOLS & set(names)
    assert MODULE in discovery.inactive and discovery.active == ()


@pytest.mark.asyncio
async def test_real_discovery_with_only_this_module_switched_off_drops_exactly_its_tools() -> None:
    discovery = discover(Settings(source_modules=[f"-{MODULE}"]))

    names = await _in_process_tool_names([f"-{MODULE}"])

    assert not TOOLS & set(names)
    assert MODULE in discovery.inactive
    assert MODULE not in [loaded.module.name for loaded in discovery.active]


# --- a real STDIO server -----------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    "value",
    [
        pytest.param(None, id="unset: every installed module is active"),
        pytest.param(f"-*,{MODULE}", id="-*,saos runs only this module"),
        pytest.param(MODULE, id="a bare name leaves everything on"),
        pytest.param("-reference", id="another module off, this one on"),
        pytest.param(f"-*,{MODULE},-{MODULE},{MODULE}", id="the last token wins"),
    ],
)
async def test_the_module_is_active_adds_its_block_and_exactly_its_two_tools(value: str | None) -> None:
    names, instructions = await _stdio(value)
    names_without_module, instructions_without = await _stdio(_with_module_off(value))

    assert set(names) - set(names_without_module) == TOOLS
    assert len(names) == len(names_without_module) + len(TOOLS)
    assert BLOCK in instructions and BLOCK not in instructions_without
    assert f"DOSTĘPNE NARZĘDZIA ({len(names)}):" in instructions
    assert all(tool in instructions for tool in TOOLS)


@pytest.mark.integration
@pytest.mark.anyio
async def test_only_this_module_gives_the_core_tool_set_plus_its_two_tools() -> None:
    names, _ = await _stdio(f"-*,{MODULE}")
    core_names, _ = await _stdio("-*")

    assert len(core_names) == CORE_TOOL_COUNT
    assert set(names) == set(core_names) | TOOLS
    assert len(names) == CORE_TOOL_COUNT + len(TOOLS)


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    "value",
    [
        pytest.param(f"-{MODULE}", id="-saos"),
        pytest.param("-*", id="-*"),
        pytest.param(f"{MODULE},-*", id="saos,-*"),
    ],
)
async def test_a_switched_off_module_leaves_no_tool_and_no_block(value: str) -> None:
    names, instructions = await _stdio(value)

    assert not TOOLS & set(names)
    assert BLOCK not in instructions
    assert not any(tool in instructions for tool in TOOLS)
    assert f"DOSTĘPNE NARZĘDZIA ({len(names)}):" in instructions


@pytest.mark.integration
@pytest.mark.anyio
async def test_with_every_module_off_the_instructions_equal_the_core_text() -> None:
    from law_scrapper_mcp.server import SERVER_INSTRUCTIONS

    _, instructions = await _stdio("-*")

    assert instructions == SERVER_INSTRUCTIONS


# --- a disabled module is never imported -------------------------------------------------------


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
        pytest.param(f"-{MODULE}", False, id="-saos"),
        pytest.param("-*", False, id="-*"),
        pytest.param(f"{MODULE},-*", False, id="saos,-*"),
        pytest.param(None, True, id="unset"),
        pytest.param(f"-*,{MODULE}", True, id="-*,saos"),
    ],
)
def test_a_switched_off_module_is_never_imported(value: str | None, imported: bool) -> None:
    assert _package_imported_after_import_of_server(value) is imported
