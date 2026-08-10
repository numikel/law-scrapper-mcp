"""Tests for MCP server registration and lifespan."""

from __future__ import annotations

import httpx
import pytest
from mcp import Client

from law_scrapper_mcp.context import AppContext
from law_scrapper_mcp.server import app, lifespan

pytestmark = pytest.mark.asyncio

EXPECTED_TOOLS = sorted(
    [
        "analyze_act_relationships",
        "browse_acts",
        "calculate_legal_date",
        "compare_acts",
        "filter_results",
        "get_act_details",
        "get_system_metadata",
        "list_loaded_documents",
        "list_result_sets",
        "read_act_content",
        "search_in_act",
        "search_legal_acts",
        "track_legal_changes",
    ]
)

async def test_all_tools_registered() -> None:
    """list_tools() returns exactly 13 tools."""
    async with Client(app) as client:
        tools = (await client.list_tools()).tools
        tool_names = sorted(tool.name for tool in tools)

    assert len(tool_names) == 13
    assert tool_names == EXPECTED_TOOLS


async def test_tool_names_match_expected() -> None:
    """Tool names match the MCP contract."""
    async with Client(app) as client:
        tools = (await client.list_tools()).tools
        tool_names = {tool.name for tool in tools}

    assert tool_names == set(EXPECTED_TOOLS)


async def test_all_tools_have_concrete_output_schemas() -> None:
    async with Client(app) as client:
        tools = (await client.list_tools()).tools

    assert len(tools) == 13
    for tool in tools:
        assert tool.output_schema is not None
        assert set(tool.output_schema["properties"]) >= {"data", "hints", "metadata"}
        assert set(tool.output_schema["properties"]) != {"result"}


async def test_lifespan_yields_services() -> None:
    """Lifespan initializes all required service attributes."""
    async with lifespan(app) as app_context:
        assert isinstance(app_context, AppContext)
        assert app_context.metadata_service is not None
        assert app_context.search_service is not None
        assert app_context.act_service is not None
        assert app_context.comparison_service is not None
        assert app_context.relationship_service is not None
        assert app_context.date_service is not None


async def test_lifespan_result_store_receives_configured_limits(monkeypatch) -> None:
    """Use non-default values on purpose."""
    from law_scrapper_mcp import server as server_module

    monkeypatch.setattr(server_module.settings, "max_pattern_length", 256)
    monkeypatch.setattr(server_module.settings, "filter_max_records", 42)

    async with lifespan(app) as app_context:
        store = app_context.result_store
        assert store.max_pattern_length == 256
        assert store.pattern_length_limit_clamped is False
        assert store.max_records == 42


async def test_lifespan_result_store_uses_clamped_limit(monkeypatch) -> None:
    """Out-of-range configuration reaches ResultStore as the effective value."""
    from law_scrapper_mcp import server as server_module

    monkeypatch.setattr(server_module.settings, "max_pattern_length", 10000)
    monkeypatch.setattr(server_module.settings, "filter_max_records", 7)

    async with lifespan(app) as app_context:
        store = app_context.result_store

        assert store.max_pattern_length == 4096
        assert store.pattern_length_limit_clamped is True
        assert store.max_records == 7


async def test_lifespan_result_store_floors_zero_record_limit(monkeypatch) -> None:
    """The Task 3 floor must apply, not be bypassed via the raw field."""
    from law_scrapper_mcp import server as server_module

    monkeypatch.setattr(server_module.settings, "filter_max_records", 0)

    async with lifespan(app) as app_context:
        assert app_context.result_store.max_records == 1


async def test_lifespan_warns_about_clamped_pattern_limit(monkeypatch, caplog) -> None:
    """Clamping must be visible in the startup log."""
    import logging

    from law_scrapper_mcp import server as server_module

    monkeypatch.setattr(server_module.settings, "max_pattern_length", 10000)

    with caplog.at_level(logging.WARNING):
        async with lifespan(app):
            pass

    assert "10000" in caplog.text
    assert "4096" in caplog.text


async def test_lifespan_closes_client_when_body_raises() -> None:
    """An exception inside `async with lifespan(app)` must not skip cleanup."""
    httpx_client: httpx.AsyncClient | None = None

    with pytest.raises(RuntimeError, match="boom"):
        async with lifespan(app) as app_context:
            httpx_client = app_context.client._client
            raise RuntimeError("boom")

    assert httpx_client is not None
    assert httpx_client.is_closed is True
