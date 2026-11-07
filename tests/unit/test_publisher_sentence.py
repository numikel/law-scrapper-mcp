"""The sentence about source publishers in the tool descriptions, and the text it replaces."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio

# The same words sit in three tools, written out in each (a shared constant would need a module
# under `tools/`, and the architecture test treats every file there as a tool module).
SENTENCE = (
    "Aktywne moduły źródeł dodają własne kody i nazwy (np. dzienniki wojewódzkie: POL_WOJ_PM lub 'pomorskie'); "
    "pełna lista: get_system_metadata(category='publishers')."
)


async def _publisher_description(mcp_client, tool: str) -> str:
    tools = (await mcp_client.list_tools()).tools
    found = next(item for item in tools if item.name == tool)
    return found.input_schema["properties"]["publisher"]["description"]


@pytest.mark.parametrize("tool", ["search_legal_acts", "browse_acts", "track_legal_changes"])
async def test_the_publisher_parameter_carries_the_same_sentence_in_every_tool(mcp_client, tool: str) -> None:
    description = await _publisher_description(mcp_client, tool)

    assert SENTENCE in description
    assert description.startswith("Kod wydawcy: 'DU' (Dziennik Ustaw) lub 'MP' (Monitor Polski).")


async def test_the_metadata_tool_no_longer_claims_the_publishers_are_only_du_and_mp(mcp_client) -> None:
    tools = (await mcp_client.list_tools()).tools
    metadata = next(item for item in tools if item.name == "get_system_metadata")
    description = metadata.input_schema["properties"]["category"]["description"]

    assert "'publishers' (wydawcy: DU, MP oraz kody i nazwy dodane przez aktywne moduły źródeł)" in description
    assert "(wydawcy: DU, MP)" not in description
    assert "aktywnych modułów źródeł" in (metadata.description or "")
