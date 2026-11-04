"""Helpers shared by the module tests: the recorded journal payloads and the hosts they came from.

A separate module, not `conftest`, so tests can import from it by a name no other module's tests use
(several module test directories are collected in one run).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
from mcp import Client
from mcp.types import Tool

from law_scrapper_mcp.client.egress import CallerIdentity, EgressHub
from law_scrapper_mcp.config import Settings
from law_scrapper_mcp.services.act_sources import ActSourceRegistry
from law_scrapper_mcp.source_modules.contract import ModuleHost
from law_scrapper_mcp.source_modules.discovery import discover

FIXTURES = Path(__file__).parents[3] / "tests" / "fixtures" / "edziennik"
CALLER = CallerIdentity(module="law-scrapper-mcp-voivodeship-journals", version="0.0.0")

PM = "https://edziennik.gdansk.uw.gov.pl/api/eli"
KP = "https://edzienniki.bydgoszcz.uw.gov.pl/api/eli"
SK = "https://edziennik.kielce.uw.gov.pl/api/eli"
MZ = "https://edziennik.mazowieckie.pl/api/eli"


def load_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@dataclass
class Started:
    """The module after its start hook ran."""

    hub: EgressHub
    registry: ActSourceRegistry
    host: ModuleHost


def synthetic_pdf(lines: list[str]) -> bytes:
    """A minimal one-page PDF whose text layer is `lines` (ASCII only), one text line each.

    Synthetic on purpose and the only synthetic payload of the module tests: no recorded journal PDF
    has `Art.` headings or a `(cid:N)` layer, and the section index and the garbled-layer path need
    exactly those. Everything else the tests feed the module is a recording.
    """

    def literal(text: str) -> str:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        return f"({escaped})"

    stream = "BT /F1 12 Tf 14 TL 50 780 Td " + " T* ".join(f"{literal(line)} Tj" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body = b"%PDF-1.4\n"
    offsets = []
    for number, content in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n{content}\nendobj\n".encode("latin-1")
    xref = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1")
    for offset in offsets:
        body += f"{offset:010d} 00000 n \n".encode("latin-1")
    body += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("latin-1")
    return body


@dataclass
class Session:
    client: Client
    tools: dict[str, Tool]

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool, require success and validate the payload against the tool's `outputSchema`."""
        result = await self.client.call_tool(name, arguments)
        assert result.is_error is False, result.content
        payload = result.structured_content
        assert payload is not None
        schema = self.tools[name].output_schema
        assert schema is not None, name
        jsonschema.validate(payload, schema)
        assert isinstance(payload["hints"], list)
        return payload["data"]

    async def fail(self, name: str, arguments: dict[str, Any]) -> str:
        """Call a tool that must refuse; return the text the agent reads."""
        result = await self.client.call_tool(name, arguments)
        assert result.is_error is True, result.structured_content
        assert result.structured_content is None
        return str(result.content[0].text)  # type: ignore[union-attr]


@asynccontextmanager
async def journal_session() -> AsyncIterator[Session]:
    """The real server with only this module active, behind an in-memory client.

    A context manager rather than a fixture: the client's task scopes must be entered and left by the
    task that runs the test.
    """
    from law_scrapper_mcp.server import build_app

    discovery = discover(Settings(source_modules=["-*", "voivodeship-journals"]))
    assert [loaded.module.name for loaded in discovery.active] == ["voivodeship-journals"]
    async with Client(build_app(discovery.active, inactive=discovery.inactive)) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        yield Session(client=client, tools=tools)
