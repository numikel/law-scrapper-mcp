"""Tests for MCP server registration and lifespan."""

from __future__ import annotations

import httpx
import pytest
from fastmcp import Client

from law_scrapper_mcp.context import AppContext, require_app_context
from law_scrapper_mcp.server import app, lifespan

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
        tools = await client.list_tools()
        tool_names = sorted(tool.name for tool in tools)

    assert len(tool_names) == 13
    assert tool_names == EXPECTED_TOOLS


async def test_tool_names_match_expected() -> None:
    """Tool names match the MCP contract."""
    async with Client(app) as client:
        tools = await client.list_tools()
        tool_names = {tool.name for tool in tools}

    assert tool_names == set(EXPECTED_TOOLS)


async def test_lifespan_yields_services() -> None:
    """Lifespan initializes all required service attributes."""
    async with lifespan(app) as lifespan_context:
        context = require_app_context(lifespan_context)
        assert isinstance(context, AppContext)
        assert context.metadata_service is not None
        assert context.search_service is not None
        assert context.act_service is not None
        assert context.comparison_service is not None
        assert context.relationship_service is not None
        assert context.date_service is not None


async def test_lifespan_result_store_receives_configured_limits(monkeypatch) -> None:
    """Use non-default values on purpose.

    512/False/100 are both the `Settings` defaults AND the `ResultStore`
    constructor defaults, so a defaults-only test still passes after the
    injection is removed (false positive) and is sensitive to ambient
    `LAW_MCP_MAX_PATTERN_LENGTH`/`LAW_MCP_FILTER_MAX_RECORDS` in the runner
    environment (false negative). Values 256/42 match neither source, so the
    test fails if the injection disappears from server.py.
    """
    from law_scrapper_mcp import server as server_module

    monkeypatch.setattr(server_module.settings, "max_pattern_length", 256)
    monkeypatch.setattr(server_module.settings, "filter_max_records", 42)

    async with lifespan(app) as lifespan_context:
        store = require_app_context(lifespan_context).result_store
        assert store.max_pattern_length == 256
        assert store.pattern_length_limit_clamped is False
        assert store.max_records == 42


async def test_lifespan_result_store_uses_clamped_limit(monkeypatch) -> None:
    """Out-of-range configuration reaches ResultStore as the effective value."""
    from law_scrapper_mcp import server as server_module

    monkeypatch.setattr(server_module.settings, "max_pattern_length", 10000)
    monkeypatch.setattr(server_module.settings, "filter_max_records", 7)

    async with lifespan(app) as lifespan_context:
        store = require_app_context(lifespan_context).result_store

        assert store.max_pattern_length == 4096
        assert store.pattern_length_limit_clamped is True
        assert store.max_records == 7


async def test_lifespan_result_store_floors_zero_record_limit(monkeypatch) -> None:
    """The Task 3 floor must apply, not be bypassed via the raw field.

    Without it, `LAW_MCP_FILTER_MAX_RECORDS=0` would permanently break
    filter_results: every call on a non-empty set would be refused. This test
    fails if server.py injects `settings.filter_max_records` instead of
    `settings.effective_filter_max_records`.
    """
    from law_scrapper_mcp import server as server_module

    monkeypatch.setattr(server_module.settings, "filter_max_records", 0)

    async with lifespan(app) as lifespan_context:
        assert require_app_context(lifespan_context).result_store.max_records == 1


async def test_lifespan_warns_about_clamped_pattern_limit(monkeypatch, caplog) -> None:
    """Clamping must be visible in the startup log"""
    import logging

    from law_scrapper_mcp import server as server_module

    monkeypatch.setattr(server_module.settings, "max_pattern_length", 10000)

    with caplog.at_level(logging.WARNING):
        async with lifespan(app):
            pass

    assert "10000" in caplog.text
    assert "4096" in caplog.text


async def test_lifespan_closes_client_when_body_raises() -> None:
    """An exception inside `async with lifespan(app)` must not skip cleanup.

    Without `try/finally` around `yield`, an error in the block (e.g. a failed
    assertion in a test, or a production failure) skipped `await client.close()`
    and left an open `httpx.AsyncClient`. The assertion checks the public
    `httpx.AsyncClient.is_closed` state, not merely that close() was called.
    """
    httpx_client: httpx.AsyncClient | None = None

    with pytest.raises(RuntimeError, match="boom"):
        async with lifespan(app) as lifespan_context:
            httpx_client = require_app_context(lifespan_context).client._client
            raise RuntimeError("boom")

    assert httpx_client is not None
    assert httpx_client.is_closed is True
