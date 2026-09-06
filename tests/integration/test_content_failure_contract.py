"""Protocol-level contract for content loading failures (cluster 10, A4/A15).

The in-memory `mcp.Client` is the only place these properties are observable:
`is_error` and `structured_content` are protocol fields, not service returns.
This module builds its own respx routes rather than reusing `mock_api_responses`,
because it needs an act whose text endpoint fails.
"""

from __future__ import annotations

from typing import Any

import pytest
import respx
from httpx import Response

from mcp_helpers import parse_tool_result

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

ACT_URL = "https://api.sejm.gov.pl/eli/acts/DU/2024/1"

FAILING_ACT = {
    "ELI": "DU/2024/1",
    "publisher": "DU",
    "year": 2024,
    "pos": 1,
    "title": "Ustawa testowa z niedostępną treścią",
    "status": "akt obowiązujący",
    "type": "Ustawa",
    "textHTML": "/eli/acts/DU/2024/1/text.html",
    "textPDF": "/eli/acts/DU/2024/1/text.pdf",
}


@pytest.fixture
async def failing_content_client() -> Any:
    """In-memory MCP client whose act text endpoint always answers 503."""
    from mcp import Client

    from law_scrapper_mcp.server import app

    with respx.mock:
        respx.get(ACT_URL).mock(return_value=Response(200, json=FAILING_ACT))
        respx.get(f"{ACT_URL}/struct").mock(return_value=Response(404))
        respx.get(f"{ACT_URL}/text.html").mock(return_value=Response(503, text="maintenance"))
        async with Client(app) as client:
            yield client


async def test_transient_content_failure_is_a_protocol_error(failing_content_client: Any) -> None:
    """An unreachable upstream ends the call, it does not shrink the payload (A4).

    Runtime note: the client's retry loop runs its full budget before giving up,
    so this test takes a few seconds. That is the production path, not a defect.

    Message note: A4's own text asks for "a message of the `unavailable` category" —
    not the `upstream` category's `_UPSTREAM_MESSAGE`, which belongs to a different
    exception family. That message is `ApiUnavailableError`'s own text, e.g.
    "API Sejmu chwilowo niedostępne (HTTP 503)", with Task 6's per-category
    remediation sentence appended after it (spec D4/A12) rather than replacing it,
    so asserting on the existing prefix still holds.
    """
    result = await failing_content_client.call_tool("get_act_details", {"eli": "DU/2024/1", "load_content": True})

    assert result.is_error is True
    assert result.structured_content is None
    payload = str(result.content)
    assert "content_status" not in payload
    assert "Ustawa testowa" not in payload
    assert "niedostępne (HTTP 503)" in payload


async def test_tool_failures_are_protocol_errors_not_success_bodies(mcp_client: Any) -> None:
    """A domain failure must never be an `error` key inside a success body (A15, F16).

    `read_act_content` on a document that was never loaded raises
    `DocumentNotLoadedError` — a plain domain exception, exactly the shape v3.0.0
    changed from a `{data, hints, error}` envelope into a protocol error.
    """
    result = await mcp_client.call_tool("read_act_content", {"eli": "DU/2024/1"})

    assert result.is_error is True
    assert result.structured_content is None


async def test_a_successful_call_still_carries_no_error_key(mcp_client: Any) -> None:
    """The success envelope stays `{data, hints}` — the F16 fix, frozen (A15)."""
    result = await mcp_client.call_tool("get_act_details", {"eli": "DU/2024/1"})

    payload = parse_tool_result(result)
    assert set(payload) >= {"data", "hints"}
    assert "error" not in payload
    assert payload["data"]["content_status"] == "not_requested"
