"""F15 freeze: every tool's structuredContent validates against its outputSchema.

The SDK client validates structured output on its own side today, but that is a
detail of the client, not a guarantee of this repository. These tests make the
contract explicit and independent of the client version.

The text block staying a full JSON copy of structuredContent is a deliberate
decision (spec D6, following the MCP 2026-07-28 backwards-compatibility advice),
not an accident of the SDK.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from mcp_helpers import parse_tool_result

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

LOAD_ACT = ("get_act_details", {"eli": "DU/2024/1", "load_content": True})

# Tool -> (setup call or None, arguments). The setup call runs first on the same
# client; for filter_results it also supplies the result_set_id.
CALLS: dict[str, tuple[tuple[str, dict[str, Any]] | None, dict[str, Any]]] = {
    "get_system_metadata": (None, {"category": "publishers"}),
    "search_legal_acts": (None, {"year": 2024}),
    "browse_acts": (None, {"publisher": "DU", "year": 2024}),
    "filter_results": (("search_legal_acts", {"year": 2024}), {"type_equals": "Ustawa"}),
    "get_act_details": (None, {"eli": "DU/2024/1"}),
    "read_act_content": (LOAD_ACT, {"eli": "DU/2024/1"}),
    "search_in_act": (LOAD_ACT, {"eli": "DU/2024/1", "query": "Content"}),
    "analyze_act_relationships": (None, {"eli": "DU/2024/1"}),
    "track_legal_changes": (None, {"date_from": "2024-01-01", "date_to": "2024-12-31"}),
    "calculate_legal_date": (None, {"days": 1, "base_date": "2026-01-01"}),
    "compare_acts": (None, {"eli_a": "DU/2024/1", "eli_b": "DU/2024/2"}),
    "list_result_sets": (None, {}),
    "list_loaded_documents": (None, {}),
}


async def _schema(mcp_client, name: str) -> dict[str, Any]:
    tools = {tool.name: tool for tool in (await mcp_client.list_tools()).tools}
    schema = tools[name].output_schema
    assert schema is not None, f"{name} has no outputSchema"
    return schema


async def _call(mcp_client, name: str):
    setup, arguments = CALLS[name]
    if setup is not None:
        setup_payload = parse_tool_result(await mcp_client.call_tool(*setup))
        if name == "filter_results":
            arguments = {**arguments, "result_set_id": setup_payload["data"]["result_set_id"]}
    return await mcp_client.call_tool(name, arguments)


async def test_every_tool_has_a_contract_call(mcp_client) -> None:
    listed = {tool.name for tool in (await mcp_client.list_tools()).tools}

    assert listed == set(CALLS)


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_structured_content_validates_against_output_schema(mcp_client, name: str) -> None:
    schema = await _schema(mcp_client, name)
    payload = parse_tool_result(await _call(mcp_client, name))

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)
    assert set(schema["properties"]) != {"result"}
    assert "x-fastmcp-wrap-result" not in json.dumps(schema)


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_text_block_is_the_full_json_copy(mcp_client, name: str) -> None:
    result = await _call(mcp_client, name)
    payload = parse_tool_result(result)

    assert result.content[0].type == "text"
    assert json.loads(result.content[0].text) == payload


async def test_the_validator_can_actually_fail(mcp_client) -> None:
    """Guard the guard: a payload without the required `data` must not validate."""
    schema = await _schema(mcp_client, "calculate_legal_date")
    payload = parse_tool_result(await _call(mcp_client, "calculate_legal_date"))
    broken = {key: value for key, value in payload.items() if key != "data"}

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(broken)
