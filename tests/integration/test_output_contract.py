"""F15 freeze: every tool's structuredContent validates against its outputSchema.

The SDK client validates structured output on its own side today, but that is a
detail of the client, not a guarantee of this repository. These tests make the
contract explicit and independent of the client version.

The text block staying a full JSON copy of structuredContent is a deliberate
decision (spec D6, following the MCP 2026-07-28 backwards-compatibility advice),
not an accident of the SDK.

The same calls also pin `openWorldHint` to behaviour: a tool that declares
`false` must not send a single request upstream, and one that declares `true`
must. The hint is read from `tools/list`, so a new tool is covered as soon as it
has an entry in CALLS.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import respx
from httpx import Response
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from law_scrapper_mcp.client.sejm_client import SejmApiClient
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
    "list_result_sets": (("search_legal_acts", {"year": 2024}), {}),
    "list_loaded_documents": (LOAD_ACT, {}),
}

# Arguments for a server that holds nothing yet; a fetch-on-miss would show up here.
COLD_OVERRIDES: dict[str, dict[str, Any]] = {
    "filter_results": {"result_set_id": "rs_999"},
}


async def _schema(mcp_client, name: str) -> dict[str, Any]:
    tools = {tool.name: tool for tool in (await mcp_client.list_tools()).tools}
    schema = tools[name].output_schema
    assert schema is not None, f"{name} has no outputSchema"
    return schema


async def _prepare(mcp_client, name: str) -> dict[str, Any]:
    """Run the setup call, if any, and return the arguments for the tool call."""
    setup, arguments = CALLS[name]
    if setup is not None:
        setup_payload = parse_tool_result(await mcp_client.call_tool(*setup))
        if name == "filter_results":
            arguments = {**arguments, "result_set_id": setup_payload["data"]["result_set_id"]}
    return arguments


async def _call(mcp_client, name: str):
    return await mcp_client.call_tool(name, await _prepare(mcp_client, name))


async def test_every_tool_has_a_contract_call(mcp_client) -> None:
    listed = {tool.name for tool in (await mcp_client.list_tools()).tools}

    assert listed == set(CALLS)


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_structured_content_validates_against_output_schema(mcp_client, name: str) -> None:
    schema = await _schema(mcp_client, name)
    payload = parse_tool_result(await _call(mcp_client, name))

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)
    # tripwire: a single-"result" wrapper or an x-fastmcp-wrap-result marker would mean the envelope got re-wrapped (F15, now historical since the fastmcp migration in v3.0.0).
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


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_no_top_level_metadata_in_schema_or_payload(mcp_client, name: str) -> None:
    """D7: the dead `EnrichedResponse.metadata` field is gone.

    Top level only: `get_system_metadata` legitimately returns `data.metadata`.
    """
    schema = await _schema(mcp_client, name)
    payload = parse_tool_result(await _call(mcp_client, name))

    assert "metadata" not in schema["properties"]
    assert "metadata" not in payload


@pytest.fixture
def upstream(mcp_client) -> respx.models.CallList:
    """Every request that reaches the httpx transport, whether a fixture route matches it or not.

    respx records a call only once it resolves: an unrouted request raises
    AllMockedAssertionError before it is recorded, and the tool would report
    that as an ordinary error result. The trailing catch-all route makes such a
    request countable. It is added after the fixture routes, so it matches only
    what they do not.
    """
    respx.route().mock(return_value=Response(418))
    return respx.calls


@pytest.fixture
def api_client_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Every SejmApiClient fetch, including those the TTL cache answers without touching the transport.

    Every public fetch method of the client delegates to one of these three.
    """
    calls: list[tuple[str, str]] = []
    for method in ("get_json", "get_text", "get_bytes"):
        original = getattr(SejmApiClient, method)

        async def spy(self, path, *args, _original=original, _method=method, **kwargs):
            calls.append((_method, path))
            return await _original(self, path, *args, **kwargs)

        monkeypatch.setattr(SejmApiClient, method, spy)
    return calls


async def test_an_unrouted_request_is_still_counted(mcp_client, upstream) -> None:
    """Guard the guard: without the catch-all route in `upstream`, respx would not record this request."""
    before = upstream.call_count
    result = await mcp_client.call_tool("get_act_details", {"eli": "DU/2099/7"})

    assert result.is_error is True
    assert upstream.call_count - before >= 1


async def _open_world_hint(mcp_client, name: str) -> bool:
    tools = {tool.name: tool for tool in (await mcp_client.list_tools()).tools}
    annotations = tools[name].annotations
    assert annotations is not None and annotations.open_world_hint is not None, name
    return annotations.open_world_hint


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_open_world_hint_matches_upstream_traffic(mcp_client, upstream, name: str) -> None:
    """On a cold server, openWorldHint=true tools reach api.sejm.gov.pl and false ones never do.

    The result itself is not checked: a closed-world tool is expected to fail
    when nothing is loaded, and that failure must not come from a fetch.
    """
    open_world = await _open_world_hint(mcp_client, name)
    arguments = {**CALLS[name][1], **COLD_OVERRIDES.get(name, {})}

    before = upstream.call_count
    await mcp_client.call_tool(name, arguments)
    requests = upstream.call_count - before

    if open_world:
        assert requests > 0, f"{name} declares openWorldHint=true but sent no request"
    else:
        assert requests == 0, f"{name} declares openWorldHint=false but sent {requests} request(s)"


@pytest.mark.parametrize("name", sorted(name for name, (setup, _) in CALLS.items() if setup is not None))
async def test_tools_that_read_server_state_stay_local_on_a_warm_server(
    mcp_client, upstream, api_client_calls, name: str
) -> None:
    """A tool that works on a result set or a loaded document is closed-world and stays that way once they exist.

    Counted at the client as well as at the transport: the setup call fills the
    TTL cache, so a fetch it answers would never reach the transport here, yet
    would reach api.sejm.gov.pl once the entry expires.
    """
    assert await _open_world_hint(mcp_client, name) is False, (
        f"{name} needs server state (it has a setup call in CALLS) but declares openWorldHint=true; "
        "exclude it from this test explicitly"
    )
    arguments = await _prepare(mcp_client, name)

    before, client_before = upstream.call_count, len(api_client_calls)
    parse_tool_result(await mcp_client.call_tool(name, arguments))

    assert upstream.call_count == before, f"{name} sent {upstream.call_count - before} request(s) on a warm server"
    assert api_client_calls[client_before:] == [], f"{name} invoked the Sejm API client on a warm server"
