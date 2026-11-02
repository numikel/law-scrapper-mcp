"""The reference tool: descriptor, state accessor and the tool contract, through a real in-memory client."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
from mcp import Client
from mcp.server import MCPServer

from law_scrapper_mcp.config import Settings
from law_scrapper_mcp.context import AppContext
from law_scrapper_mcp.source_modules.contract import LoadedModule
from law_scrapper_mcp.source_modules.discovery import discover
from law_scrapper_mcp_reference import module
from law_scrapper_mcp_reference.state import MODULE_NAME, ReferenceState, reference_state


def _server() -> MCPServer[AppContext]:
    # Imported here: importing the server runs discovery, and a broken descriptor must fail a named test.
    from law_scrapper_mcp.server import build_app

    return build_app([_loaded()])


def _loaded() -> LoadedModule:
    (item,) = discover(Settings(source_modules=[])).active
    return item


def test_the_descriptor_declares_the_name_and_the_one_tool() -> None:
    assert module.name == "reference" == MODULE_NAME
    assert module.tool_names == ("reference_ping",)
    assert module.start is not None


def test_the_accessor_refuses_a_context_without_the_modules_state() -> None:
    context = Mock(spec=AppContext, module_state={})

    with pytest.raises(RuntimeError, match="reference"):
        reference_state(context)


def test_the_accessor_returns_the_state_the_start_hook_left() -> None:
    state = ReferenceState(module="law-scrapper-mcp-reference", version="0.0.0")
    context = Mock(spec=AppContext, module_state={MODULE_NAME: state})

    assert reference_state(context) is state


@pytest.mark.asyncio
async def test_reference_ping_reports_the_started_module_and_passes_output_schema_validation() -> None:
    server = _server()

    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        result = await client.call_tool("reference_ping", {"echo": "moduł działa"})

    ping = tools["reference_ping"]
    assert ping.title == "Sprawdzenie modułu referencyjnego"
    assert ping.annotations is not None
    assert ping.annotations.read_only_hint is True
    assert ping.output_schema is not None
    assert set(ping.output_schema["properties"]) == {"data", "hints"}
    assert result.is_error is False
    payload: dict[str, Any] = result.structured_content or {}
    assert payload["data"] == {
        "module": "law-scrapper-mcp-reference",
        "version": "0.0.0",
        "started": True,
        "echo": "moduł działa",
    }
    assert payload["hints"], "every tool answers with hints"


@pytest.mark.asyncio
async def test_reference_ping_without_echo_returns_null_echo() -> None:
    async with Client(_server()) as client:
        result = await client.call_tool("reference_ping", {})

    assert result.is_error is False
    assert (result.structured_content or {})["data"]["echo"] is None
