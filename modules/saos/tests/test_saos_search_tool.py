"""`search_saos_judgments` through the MCP protocol: registration, schemas, output and errors."""

from __future__ import annotations

import re
from typing import Any

import jsonschema
import pytest
import respx
from mcp import Client
from saos_testing import FakeSaos, connected, corpus, loaded

from law_scrapper_mcp_saos.service import COVERAGE_SENTENCE

CORE_TOOLS = 13
MODULE_TOOLS = 2
TOOL = "search_saos_judgments"


async def _tool(client: Client) -> Any:
    return next(tool for tool in (await client.list_tools()).tools if tool.name == TOOL)


@pytest.mark.asyncio
async def test_the_server_lists_the_core_tools_and_the_module_tool(saos_router: respx.MockRouter) -> None:
    async with connected(saos_router) as client:
        names = [tool.name for tool in (await client.list_tools()).tools]

        assert TOOL in names
        assert len(names) == CORE_TOOLS + MODULE_TOOLS


@pytest.mark.asyncio
async def test_the_tool_has_a_polish_title_a_remote_read_only_annotation_and_a_typed_output(
    saos_router: respx.MockRouter,
) -> None:
    async with connected(saos_router) as client:
        tool = await _tool(client)

        assert tool.title == "Wyszukiwanie orzeczeń w SAOS"
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.open_world_hint is True
        assert tool.output_schema is not None
        assert set(tool.output_schema["properties"]) == {"data", "hints"}


@pytest.mark.asyncio
async def test_the_input_schema_has_exactly_the_documented_parameters_with_descriptions(
    saos_router: respx.MockRouter,
) -> None:
    async with connected(saos_router) as client:
        tool = await _tool(client)
        properties = tool.input_schema["properties"]

        assert set(properties) == {
            "phrase",
            "signature",
            "date_from",
            "date_to",
            "court_type",
            "judgment_type",
            "limit",
            "offset",
            "include_portal_links",
            "labour_only",
        }
        for name, schema in properties.items():
            assert schema.get("description", "").strip(), name
        assert properties["limit"]["default"] == 20
        assert properties["offset"]["default"] == 0
        assert properties["include_portal_links"]["default"] is False
        assert properties["labour_only"]["default"] is False
        for fact in ("5", "100", "scan", "include_portal_links", "Sądu Najwyższego", "SUPREME"):
            assert fact in properties["labour_only"]["description"], fact
        assert "scan.next_offset" in properties["offset"]["description"]
        assert "get_saos_judgment_details" in properties["include_portal_links"]["description"]
        assert "5" in properties["include_portal_links"]["description"]
        assert "saos.org.pl" in properties["phrase"]["description"]
        assert "saos.org.pl" in properties["signature"]["description"]
        assert "100" in properties["limit"]["description"]
        assert not tool.input_schema.get("required")


@pytest.mark.asyncio
async def test_the_description_carries_the_decision_tree_five_examples_and_the_dated_coverage(
    saos_router: respx.MockRouter,
) -> None:
    async with connected(saos_router) as client:
        description = (await _tool(client)).description or ""

        assert "Kiedy użyć:" in description
        assert "Kiedy NIE używać:" in description
        assert len(re.findall(rf"^\s*- {TOOL}\(", description, flags=re.MULTILINE)) >= 5
        assert "saos.org.pl" in description
        assert "(15|18|21)$" in description
        assert "(15|21)$" in description
        assert "labour_only" in description
        assert "Kiedy NIE używać labour_only:" in description
        # The docstring repeats the coverage figures of the hint; both must stay one set of numbers.
        for figure in re.findall(r"\d{1,3}(?: \d{3})+", COVERAGE_SENTENCE):
            assert figure in description, figure
        assert "2026-11-02" in description


def test_the_instructions_name_the_tool_and_count_it() -> None:
    from law_scrapper_mcp.server import build_app

    server = build_app([loaded()])

    assert server.instructions is not None
    assert f"DOSTĘPNE NARZĘDZIA ({CORE_TOOLS + MODULE_TOOLS}):" in server.instructions
    assert "MODUŁ ŹRÓDŁA — saos (narzędzi: 2)" in server.instructions
    assert TOOL in server.instructions


@pytest.mark.asyncio
async def test_a_call_returns_structured_content_that_validates_against_the_output_schema(
    saos_router: respx.MockRouter,
) -> None:
    async with connected(saos_router) as client:
        tool = await _tool(client)

        result = await client.call_tool(TOOL, {"phrase": "wypadek", "limit": 5})

        assert result.is_error is False
        payload = result.structured_content
        assert payload is not None
        jsonschema.validate(payload, tool.output_schema)
        data = payload["data"]
        assert data["source"] == "saos"
        assert data["returned_count"] == 5 == len(data["results"])
        assert data["total_count"] == len(corpus())
        assert data["result_set_id"]
        assert data["result_set_scope"]["scope"] == "page"
        assert data["page_info"]["next_offset"] == 5
        assert payload["hints"]
        first = data["results"][0]
        assert first["source"] == "saos"
        assert first["source_url"].startswith("https://www.saos.org.pl/judgments/")
        assert first["portal_url"] is None


@pytest.mark.asyncio
async def test_the_stored_set_is_filterable_with_the_core_tool(saos_router: respx.MockRouter) -> None:
    async with connected(saos_router) as client:
        search = await client.call_tool(TOOL, {"limit": 20})
        result_set_id = (search.structured_content or {})["data"]["result_set_id"]

        narrowed = await client.call_tool(
            "filter_results", {"result_set_id": result_set_id, "pattern": "(15|18|21)$", "field": "division_code"}
        )

        assert narrowed.is_error is False
        data = (narrowed.structured_content or {})["data"]
        assert data["document_kind"] == "judgment"
        assert data["results"] == []
        assert data["judgments"]
        assert all(j["division_code"][-2:] in ("15", "18", "21") for j in data["judgments"])


@pytest.mark.asyncio
async def test_list_result_sets_shows_the_judgment_kind(saos_router: respx.MockRouter) -> None:
    async with connected(saos_router) as client:
        await client.call_tool(TOOL, {"limit": 3})

        listed = await client.call_tool("list_result_sets", {})

        sets = (listed.structured_content or {})["data"]["sets"]
        assert [entry["document_kind"] for entry in sets] == ["judgment"]


@pytest.mark.asyncio
async def test_a_validation_error_is_a_polish_tool_error(saos_router: respx.MockRouter) -> None:
    async with connected(saos_router) as client:
        result = await client.call_tool(TOOL, {"court_type": "APPEAL"})

        assert result.is_error is True
        assert result.structured_content is None
        text = result.content[0].text  # type: ignore[union-attr]
        assert "court_type" in text
        assert "Popraw parametr" in text


@pytest.mark.asyncio
async def test_a_saos_outage_is_a_tool_error_that_names_the_source(saos_router: respx.MockRouter) -> None:
    from law_scrapper_mcp.server import build_app

    FakeSaos(failing_pages={0}).install(saos_router)
    async with Client(build_app([loaded()])) as connected:
        result = await connected.call_tool(TOOL, {})

    assert result.is_error is True
    text = result.content[0].text  # type: ignore[union-attr]
    assert "SAOS" in text
    assert "Pozostałe źródła nie są objęte tym błędem" in text
