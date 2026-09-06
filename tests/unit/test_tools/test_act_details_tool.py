"""Tool-boundary tests for `get_act_details`' hint wiring (final-review I1).

`act_details_hints` already refuses to name a PDF URL when none is given, but
`tools/act_details.py` used to compute that URL unconditionally, regardless of
`has_pdf`. No existing test caught this: every direct `act_details_hints` test
in `test_response_enrichment.py` passes `pdf_url=` explicitly, so the
production wiring in `act_details.py` was never exercised for `has_pdf=False`.
"""

from __future__ import annotations

from typing import Any

import pytest
import respx
from httpx import Response

pytestmark = pytest.mark.anyio

NEITHER_FORMAT_ACT = {
    "ELI": "DU/2024/9",
    "publisher": "DU",
    "year": 2024,
    "pos": 9,
    "title": "Ustawa testowa bez formatu treści",
    "status": "akt obowiązujący",
    "type": "Ustawa",
    "textHTML": None,
    "textPDF": None,
}


@pytest.fixture
async def neither_format_client() -> Any:
    """In-memory MCP client for an act whose own metadata advertises no text at all."""
    from mcp import Client

    from law_scrapper_mcp.server import app

    with respx.mock:
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/9").mock(
            return_value=Response(200, json=NEITHER_FORMAT_ACT)
        )
        respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/9/struct").mock(return_value=Response(404))
        async with Client(app) as client:
            yield client


async def test_unavailable_hint_never_names_a_pdf_the_server_knows_is_absent(
    neither_format_client: Any,
) -> None:
    """`has_pdf=False` must not produce a hint pointing at `text.pdf`.

    `act_service._load_content` refuses to request `text.pdf` for this act on
    politeness grounds (its own metadata already proves the request pointless);
    the hint builder must not then instruct the caller to make that exact
    request itself.
    """
    result = await neither_format_client.call_tool("get_act_details", {"eli": "DU/2024/9", "load_content": True})

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["data"]["content_status"] == "unavailable"
    assert result.structured_content["data"]["has_pdf"] is False

    hint_messages = [hint["message"] for hint in result.structured_content["hints"]]
    assert not any("text.pdf" in message for message in hint_messages)
    assert not any("http://" in message or "https://" in message for message in hint_messages)
