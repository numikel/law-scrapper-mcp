"""Tests for MCP server registration and lifespan."""

from __future__ import annotations

from typing import Any

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


def _resolve(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """Follow a local `$ref` into the schema's `$defs`; return other nodes as they are."""
    ref = node.get("$ref")
    if ref is None:
        return node
    prefix = "#/$defs/"
    assert ref.startswith(prefix), f"non-local $ref {ref!r}"
    return schema["$defs"][ref[len(prefix) :]]


def _property(schema: dict[str, Any], path: str) -> dict[str, Any]:
    """Return the raw property node at `path` under the tool's `data` payload.

    Segments are property names; a trailing `[]` steps into a list's `items`.
    """
    node = _resolve(schema, schema["properties"]["data"])
    for segment in path.split("."):
        name, _, suffix = segment.partition("[")
        node = node["properties"][name]
        if suffix:
            node = _resolve(schema, node)["items"]
        node = _resolve(schema, node)
    return node


def _nullable_ref(schema: dict[str, Any], node: dict[str, Any], model: str) -> dict[str, Any]:
    """Assert `node` is an optional reference to `model` and return the referenced model."""
    assert node.get("anyOf") == [{"$ref": f"#/$defs/{model}"}, {"type": "null"}], node
    assert node.get("default") is None
    return schema["$defs"][model]


def _assert_result_set_scope(schema: dict[str, Any], node: dict[str, Any]) -> None:
    """`ResultSetScope` must reach the client with its enum and its nullable corpus size."""
    scope = node["properties"]["scope"]
    assert _resolve(schema, scope)["enum"] == ["complete", "page"]
    assert node["properties"]["corpus_count"]["anyOf"] == [{"type": "integer"}, {"type": "null"}]
    assert {"scope", "stored_count", "window_offset"} <= set(node["required"])


async def test_scope_fields_reach_the_output_schemas() -> None:
    """Criterion 20: the scope contract is typed in the schemas, and nothing was dropped.

    A substring check over the serialised schema would still pass if a field
    were retyped to `str`, moved into a description, or lost its enum; every
    property is therefore resolved through `$defs` and pinned by type.
    """
    async with Client(app) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    schemas = {name: tool.output_schema for name, tool in tools.items()}
    for name in ("search_legal_acts", "browse_acts", "track_legal_changes", "filter_results", "list_result_sets"):
        assert schemas[name] is not None, f"{name} has no output schema"

    for name in ("search_legal_acts", "browse_acts"):
        schema = schemas[name]
        assert schema is not None
        data = _resolve(schema, schema["properties"]["data"])
        scope = _nullable_ref(schema, data["properties"]["result_set_scope"], "ResultSetScope")
        _assert_result_set_scope(schema, scope)
        assert _property(schema, "total_count") == {"title": "Total Count", "type": "integer"}
        assert _property(schema, "result_set_id")["anyOf"] == [{"type": "string"}, {"type": "null"}]

    changes = schemas["track_legal_changes"]
    assert changes is not None
    data = _resolve(changes, changes["properties"]["data"])
    _assert_result_set_scope(changes, _nullable_ref(changes, data["properties"]["result_set_scope"], "ResultSetScope"))
    assert _property(changes, "date_range") == {"title": "Date Range", "type": "string"}

    filtered = schemas["filter_results"]
    assert filtered is not None
    data = _resolve(filtered, filtered["properties"]["data"])
    inconclusive = data["properties"]["no_match_is_inconclusive"]
    assert inconclusive["type"] == "boolean"
    assert inconclusive["default"] is False
    assert data["properties"]["source_scope"] == {
        "$ref": "#/$defs/ResultSetScope",
        "description": data["properties"]["source_scope"]["description"],
    }
    assert "source_scope" in data["required"]
    _assert_result_set_scope(
        filtered, _nullable_ref(filtered, data["properties"]["result_set_scope"], "ResultSetScope")
    )
    assert _property(filtered, "filtered_count") == {"title": "Filtered Count", "type": "integer"}

    listing = schemas["list_result_sets"]
    assert listing is not None
    entry = _property(listing, "sets[]")
    assert _resolve(listing, entry["properties"]["scope"])["enum"] == ["complete", "page"]
    assert "scope" in entry["required"]
    assert _property(listing, "sets[].result_count") == {"title": "Result Count", "type": "integer"}


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
