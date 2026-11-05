"""`filter_results` through the MCP protocol on a set of judgments.

The core has no judgment search tool, so a fake module seeds the result store the way a real source
module does: through `get_app_context(ctx).result_store`.
"""

from __future__ import annotations

import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.server.mcpserver import Context

from fake_source_modules import fake_module, loaded
from judgment_samples import sample_judgments
from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.models.tool_outputs import DocumentKind, SetScope
from law_scrapper_mcp.server import build_app
from mcp_helpers import parse_tool_result

pytestmark = pytest.mark.anyio


def _seeding_module(*, scope: SetScope | None = None, window_offset: int = 0):  # noqa: ANN202
    def register(server: MCPServer[AppContext]) -> None:
        @server.tool(name="seed_judgments")
        async def seed_judgments(ctx: Context[AppContext]) -> str:
            records = sample_judgments()
            result_set_id, _ = await get_app_context(ctx).result_store.store(
                records,
                "seeded judgments",
                len(records) if scope is None else 500,
                window_offset=window_offset,
                scope=scope,
                document_kind=DocumentKind.JUDGMENT,
            )
            return result_set_id

    return fake_module("seeder", ["seed_judgments"], register=register)


async def _seed(client: Client) -> str:
    result = await client.call_tool("seed_judgments", {})
    assert result.is_error is False
    return str(result.content[0].text)  # type: ignore[union-attr]


async def test_labour_pattern_narrows_a_judgment_set() -> None:
    async with Client(build_app([loaded(_seeding_module())])) as client:
        result_set_id = await _seed(client)

        result = await client.call_tool(
            "filter_results",
            {"result_set_id": result_set_id, "pattern": "(15|18|21)$", "field": "division_code"},
        )

    data = parse_tool_result(result)["data"]
    assert data["document_kind"] == "judgment"
    assert data["results"] == []
    assert [j["source_id"] for j in data["judgments"]] == ["1", "2", "4"]
    assert data["filtered_count"] == 3
    assert data["result_set_id"] is not None
    hints = parse_tool_result(result)["hints"]
    assert any(h.get("tool") == "filter_results" for h in hints)
    assert all(h.get("tool") != "get_act_details" for h in hints)


async def test_list_result_sets_shows_the_kind() -> None:
    async with Client(build_app([loaded(_seeding_module())])) as client:
        await _seed(client)

        result = await client.call_tool("list_result_sets", {})

    sets = parse_tool_result(result)["data"]["sets"]
    assert [s["document_kind"] for s in sets] == ["judgment"]


async def test_status_filter_on_judgments_is_a_polish_error() -> None:
    async with Client(build_app([loaded(_seeding_module())])) as client:
        result_set_id = await _seed(client)

        result = await client.call_tool(
            "filter_results", {"result_set_id": result_set_id, "status_equals": "akt obowiązujący"}
        )

    assert result.is_error is True
    assert "status_equals" in result.content[0].text  # type: ignore[union-attr]
    assert "tylko aktów" in result.content[0].text  # type: ignore[union-attr]


async def test_empty_match_on_a_window_carries_the_inconclusive_hint() -> None:
    module = _seeding_module(scope=SetScope.PAGE, window_offset=20)
    async with Client(build_app([loaded(module)])) as client:
        result_set_id = await _seed(client)

        result = await client.call_tool(
            "filter_results", {"result_set_id": result_set_id, "pattern": "nothing-matches", "field": "court"}
        )

    payload = parse_tool_result(result)
    assert payload["data"]["no_match_is_inconclusive"] is True
    assert any("OKNO" in h["message"] for h in payload["hints"])


async def test_description_documents_the_judgment_values() -> None:
    async with Client(build_app([loaded(_seeding_module())])) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    tool = tools["filter_results"]
    properties = tool.input_schema["properties"]
    assert "division_code" in properties["field"]["description"]
    assert "judgment_date" in properties["date_field"]["description"]
    assert "tylko dla zestawów aktów" in properties["status_equals"]["description"]
    assert "(15|18|21)$" in (tool.description or "")
    assert "(15|21)$" in (tool.description or "")
