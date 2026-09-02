"""Tests for MCP-visible tool descriptions."""

from __future__ import annotations

import pytest

from mcp_helpers import parse_tool_result

pytestmark = pytest.mark.anyio

EXPECTED_ARGUMENTS = {
    "get_system_metadata": {"category", "limit", "offset"},
    "search_legal_acts": {
        "publisher",
        "year",
        "keywords",
        "date_from",
        "date_to",
        "title",
        "act_type",
        "pub_date_from",
        "pub_date_to",
        "in_force",
        "limit",
        "offset",
        "detail_level",
    },
    "browse_acts": {"publisher", "year", "limit", "offset", "detail_level"},
    "get_act_details": {"eli", "load_content"},
    "read_act_content": {"eli", "section", "limit", "offset"},
    "list_loaded_documents": {"limit", "offset"},
    "search_in_act": {"eli", "query", "context_chars", "limit", "offset"},
    "analyze_act_relationships": {"eli", "relationship_type"},
    "track_legal_changes": {"date_from", "publisher", "date_to", "keywords", "limit", "offset"},
    "calculate_legal_date": {"days", "months", "years", "base_date"},
    "filter_results": {
        "result_set_id",
        "pattern",
        "field",
        "type_equals",
        "status_equals",
        "year_equals",
        "date_field",
        "date_from",
        "date_to",
        "sort_by",
        "sort_desc",
        "limit",
        "offset",
    },
    "list_result_sets": {"limit", "offset"},
    "compare_acts": {"eli_a", "eli_b"},
}


async def _get_tool(mcp_client, name: str):
    tools = (await mcp_client.list_tools()).tools
    return next(tool for tool in tools if tool.name == name)


class TestToolInputSchemaDescriptions:
    async def test_all_public_parameters_have_non_empty_descriptions(self, mcp_client) -> None:
        tools = (await mcp_client.list_tools()).tools

        for tool in tools:
            properties = tool.input_schema.get("properties", {})
            for param_name, param_schema in properties.items():
                description = param_schema.get("description")
                assert description and description.strip(), (
                    f"Tool '{tool.name}' parameter '{param_name}' is missing input_schema description"
                )

    async def test_unclamped_limit_is_documented(self, mcp_client) -> None:
        """P5 keeps `search_legal_acts` free of the shared 100-item clamp.

        The asymmetry is only defensible while the schema says so, otherwise a
        client reading tools/list sees thirteen identical page shapes and a
        silent exception.

        `browse_acts` used to be the second exception and no longer is — see
        `test_browse_limit_is_documented_as_clamped` below for why the premise
        under P5 changed for that tool specifically.

        Issue #19: stating the asymmetry is not enough — the schema also has to say
        *why* it exists (the value goes straight to a public institution's API) and
        what that asks of the caller (the smallest value that will do).
        """
        tool = await _get_tool(mcp_client, "search_legal_acts")
        description = tool.input_schema["properties"]["limit"]["description"]

        assert "100" in description
        assert "nie jest przycinana" in description.lower()
        assert "api.sejm.gov.pl" in description
        assert "najmniejszą" in description.lower()

    async def test_browse_limit_is_documented_as_clamped(self, mcp_client) -> None:
        """`browse_acts` left P5's exception when the endpoint under it changed.

        P5 could treat an unbounded `limit` as free because `acts/{publisher}/{year}`
        ignored it: the year arrived whole whatever the caller asked for. Since Klaster 8
        routed browse through `acts/search`, `limit` reaches the wire and decides how
        wide a page the Sejm API builds, so the same schema promise now buys real
        upstream cost. The clamp and this description have to move together — an agent
        that reads "bez górnej granicy" and gets 100 rows is being lied to.
        """
        tool = await _get_tool(mcp_client, "browse_acts")
        description = tool.input_schema["properties"]["limit"]["description"]

        assert "100" in description
        assert "bez górnej granicy" not in description.lower()
        assert "najmniejszą" in description.lower()

    async def test_tool_schemas_preserve_arguments_and_expose_typed_outputs(self, mcp_client) -> None:
        tools = (await mcp_client.list_tools()).tools
        by_name = {tool.name: tool for tool in tools}

        assert set(by_name) == set(EXPECTED_ARGUMENTS)
        for name, expected_arguments in EXPECTED_ARGUMENTS.items():
            tool = by_name[name]
            assert set(tool.input_schema["properties"]) == expected_arguments
            assert tool.output_schema is not None
            assert set(tool.output_schema["properties"]) >= {"data", "hints", "metadata"}
            assert set(tool.output_schema["properties"]) != {"result"}


class TestFilterResultsDescriptions:
    async def test_pattern_description_names_supported_subset(self, mcp_client) -> None:
        tool = await _get_tool(mcp_client, "filter_results")
        description = tool.input_schema["properties"]["pattern"]["description"]

        assert "lookaround" in description.lower()
        assert "backrefer" in description.lower()
        assert r"\p{L}" in description

    async def test_tool_description_mentions_record_limit(self, mcp_client) -> None:
        tool = await _get_tool(mcp_client, "filter_results")

        assert "100" in (tool.description or "")

    async def test_unsupported_pattern_surfaces_polish_error(self, mcp_client) -> None:
        search = await mcp_client.call_tool("search_legal_acts", {"year": 2024})
        search_payload = parse_tool_result(search)
        result_set_id = search_payload["data"]["result_set_id"]

        result = await mcp_client.call_tool(
            "filter_results",
            {"result_set_id": result_set_id, "pattern": "(?<=Ustawa)o"},
        )

        assert result.is_error is True
        assert result.structured_content is None
        assert "nie jest obsługiwany" in result.content[0].text


class TestSearchInActContextChars:
    """Issue #21: the context window bounds live in one place, `models/pagination.py`.

    The schema default, its description, the request model and the output model each
    used to spell out 500 and 2000 as literals. A literal cannot drift *visibly* —
    the schema would keep promising 500 after the constant moved — so the test pins
    every surface to the constant rather than to a number.
    """

    async def test_schema_default_and_ceiling_follow_the_shared_constants(self, mcp_client) -> None:
        from law_scrapper_mcp.models.pagination import DEFAULT_CONTEXT_CHARS, MAX_CONTEXT_CHARS

        tool = await _get_tool(mcp_client, "search_in_act")
        schema = tool.input_schema["properties"]["context_chars"]

        assert schema["default"] == DEFAULT_CONTEXT_CHARS
        assert str(DEFAULT_CONTEXT_CHARS) in schema["description"]
        assert str(MAX_CONTEXT_CHARS) in schema["description"]

    async def test_request_and_output_models_follow_the_shared_constants(self) -> None:
        from law_scrapper_mcp.models.pagination import DEFAULT_CONTEXT_CHARS, MAX_CONTEXT_CHARS
        from law_scrapper_mcp.models.tool_inputs import SearchInActRequest
        from law_scrapper_mcp.models.tool_outputs import SearchInActOutput

        request_field = SearchInActRequest.model_fields["context_chars"]
        assert request_field.default == DEFAULT_CONTEXT_CHARS
        assert str(MAX_CONTEXT_CHARS) in (request_field.description or "")
        assert SearchInActOutput.model_fields["context_chars_requested"].default == DEFAULT_CONTEXT_CHARS
        assert SearchInActOutput.model_fields["context_chars_applied"].default == DEFAULT_CONTEXT_CHARS
