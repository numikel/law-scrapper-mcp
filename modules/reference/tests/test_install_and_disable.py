"""Install and disable proof for the reference module, against the real installed distribution.

Nothing here is a fake: the entry point comes from the installed distribution metadata, and the
STDIO cases start a real `python -m law_scrapper_mcp` server and talk MCP to it.

`law_scrapper_mcp.server` is imported inside the tests on purpose: importing it runs discovery, so a
broken descriptor must fail a named test here, not the collection of the whole file.

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
MODULE_IMPORT = "law_scrapper_mcp_reference"
CORE_TOOL_COUNT = 13
# Other members may be installed next to this one, and some of them add tools. Every count below is
# therefore derived from the same configuration with `reference` switched off, never hard-coded.


async def _in_process_tool_names(tokens: list[str]) -> list[str]:
    """Tool names of a server built in-process for the given `LAW_MCP_SOURCE_MODULES` tokens."""
    from law_scrapper_mcp.server import build_app

    discovery = discover(Settings(source_modules=tokens))
    server = build_app(discovery.active, inactive=discovery.inactive)
    return sorted(tool.name for tool in await server.list_tools())


def _reference_entry_points() -> list[importlib.metadata.EntryPoint]:
    return [ep for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP) if ep.name == "reference"]


def _server_environment(value: str | None) -> dict[str, str]:
    """A clean child environment: the variable is either exactly `value` or absent."""
    environment = {key: item for key, item in os.environ.items() if key != VARIABLE}
    if value is not None:
        environment[VARIABLE] = value
    return environment


# --- the entry point and in-process discovery ---------------------------------------------------


def test_the_entry_point_is_installed_in_the_module_group() -> None:
    found = _reference_entry_points()

    assert len(found) == 1, "the member is not installed: run `uv sync --extra dev --all-packages`"
    assert found[0].value == "law_scrapper_mcp_reference:module"
    assert found[0].dist is not None
    assert found[0].dist.name == "law-scrapper-mcp-reference"
    assert found[0].dist.version == "0.0.0"


def test_the_descriptor_name_equals_the_entry_point_name() -> None:
    (entry_point,) = _reference_entry_points()

    descriptor = entry_point.load()

    assert isinstance(descriptor, SourceModule)
    assert descriptor.name == entry_point.name


@pytest.mark.asyncio
async def test_real_discovery_lists_core_plus_the_reference_tool() -> None:
    from law_scrapper_mcp.server import build_app

    discovery = discover(Settings(source_modules=[]))

    server = build_app(discovery.active, inactive=discovery.inactive)

    names = sorted(tool.name for tool in await server.list_tools())
    without_reference = await _in_process_tool_names(["-reference"])
    assert len(names) == len(without_reference) + 1
    assert set(names) - set(without_reference) == {"reference_ping"}
    assert server.instructions is not None
    assert f"DOSTĘPNE NARZĘDZIA ({len(names)}):" in server.instructions
    assert "MODUŁ ŹRÓDŁA — reference (narzędzi: 1)" in server.instructions


@pytest.mark.asyncio
async def test_real_discovery_with_every_module_switched_off_is_the_core() -> None:
    from law_scrapper_mcp.server import build_app

    discovery = discover(Settings(source_modules=["-*"]))

    server = build_app(discovery.active, inactive=discovery.inactive)

    names = [tool.name for tool in await server.list_tools()]
    assert len(names) == CORE_TOOL_COUNT
    assert "reference_ping" not in names
    assert "reference" in discovery.inactive
    assert discovery.active == ()


@pytest.mark.asyncio
async def test_real_discovery_with_only_this_module_switched_off_drops_exactly_its_tool() -> None:
    discovery = discover(Settings(source_modules=["-reference"]))

    names = await _in_process_tool_names(["-reference"])

    assert "reference_ping" not in names
    assert "reference" in discovery.inactive
    assert "reference" not in [loaded.module.name for loaded in discovery.active]


# --- a real STDIO server -----------------------------------------------------------------------


async def _stdio_tools_and_instructions(value: str | None) -> tuple[list[str], str]:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "law_scrapper_mcp"],
        cwd=str(PROJECT_ROOT),
        env={**get_default_environment(), **_server_environment(value), "LAW_MCP_TRANSPORT": "stdio"},
    )
    with open(os.devnull, "w", encoding="utf-8") as errlog:
        async with Client(stdio_client(parameters, errlog=errlog)) as client:
            tools = (await client.list_tools()).tools
            instructions = client.instructions
    assert instructions is not None
    return sorted(tool.name for tool in tools), instructions


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    ("value", "reference_active"),
    [
        pytest.param(None, True, id="unset: every installed module is active"),
        pytest.param("-reference", False, id="-reference"),
        pytest.param("-*", False, id="-* resets everything"),
        pytest.param("-*,reference", True, id="-*,reference re-enables after the reset"),
        pytest.param("reference,-reference", False, id="the last token wins"),
        pytest.param("reference", True, id="a bare name leaves the module on"),
    ],
)
async def test_stdio_server_tool_count_follows_the_variable(value: str | None, reference_active: bool) -> None:
    names, instructions = await _stdio_tools_and_instructions(value)

    # The same configuration with `reference` forced off is the yardstick, so a second installed
    # module (with or without tools) cannot change what this test expects.
    tokens = [token.strip() for token in (value or "").split(",") if token.strip()]
    others = await _in_process_tool_names([*tokens, "-reference"])
    assert len(names) == len(others) + (1 if reference_active else 0)
    assert ("reference_ping" in names) is reference_active
    assert f"DOSTĘPNE NARZĘDZIA ({len(names)}):" in instructions
    assert ("MODUŁ ŹRÓDŁA — reference" in instructions) is reference_active
    if value == "-*":
        assert len(names) == CORE_TOOL_COUNT


@pytest.mark.integration
@pytest.mark.anyio
async def test_the_disabled_server_instructions_equal_the_core_text() -> None:
    from law_scrapper_mcp.server import SERVER_INSTRUCTIONS

    _, instructions = await _stdio_tools_and_instructions("-*")

    assert instructions == SERVER_INSTRUCTIONS


# --- a disabled module is never imported -------------------------------------------------------


def _module_imported_after_import_of_server(value: str | None) -> bool:
    """Import the server in a child process and report whether the module's package was imported."""
    probe = (
        "import sys\n"
        "import law_scrapper_mcp.server\n"
        f"print('imported' if {MODULE_IMPORT!r} in sys.modules else 'absent')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=PROJECT_ROOT,
        env=_server_environment(value),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return result.stdout.strip().splitlines()[-1] == "imported"


@pytest.mark.parametrize(
    ("value", "imported"),
    [
        pytest.param("-reference", False, id="-reference"),
        pytest.param("-*", False, id="-*"),
        pytest.param("reference,-*", False, id="reference,-*"),
        pytest.param(None, True, id="unset"),
        pytest.param("-*,reference", True, id="-*,reference"),
    ],
)
def test_a_switched_off_module_is_never_imported(value: str | None, imported: bool) -> None:
    assert _module_imported_after_import_of_server(value) is imported
