"""One failing source leaves the others working, proven by the core without any module installed (FR-005).

The server runs for real, in memory: two fake source modules each register their own host through the
egress hub and expose one tool that calls it, and the Sejm answers through a core tool. Every host is
mocked by `respx`, so nothing reaches a network. The same story is told by the SAOS and voivodeship
module suites, but those need the module distributions installed; this file keeps the guarantee in the
plain `uv sync --extra dev` job.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import httpx
import pytest
import respx
from mcp import Client
from mcp.server import MCPServer
from mcp.server.mcpserver import Context

from fake_source_modules import fake_module, loaded
from law_scrapper_mcp import server as server_module
from law_scrapper_mcp.client import egress as egress_module
from law_scrapper_mcp.client.egress import EgressClient, EgressHost
from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.server import build_app
from law_scrapper_mcp.source_modules.contract import LoadedModule, ModuleHost
from law_scrapper_mcp.tools.error_handling import handle_tool_errors

pytestmark = pytest.mark.asyncio

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SEJM_SEARCH = "https://api.sejm.gov.pl/eli/acts/search"
ALPHA_API = "https://alpha.example.test/api"
BETA_API = "https://beta.example.test/api"
OTHER_SOURCES = "Pozostałe źródła nie są objęte tym błędem."
REFUSAL_GUIDANCE = "Nie ponawiaj wywołania; zgłoś użytkownikowi lub operatorowi serwera, że źródło odmawia obsługi."


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """The retry loop sleeps between attempts; an outage test must not sit those waits out."""

    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(egress_module, "_delay", instant)


def source(name: str, label: str, base_url: str) -> LoadedModule:
    """A fake module with one host labelled `label` and one tool, `<name>_ping`, that calls it."""
    tool_name = f"{name}_ping"

    @asynccontextmanager
    async def start(host: ModuleHost) -> AsyncIterator[EgressClient]:
        yield host.egress.client_for(EgressHost(name=label, base_url=base_url), host.caller)

    def register(server: MCPServer[AppContext]) -> None:
        @handle_tool_errors
        async def ping(ctx: Context[AppContext]) -> str:
            client = cast(EgressClient, get_app_context(ctx).module_state[name])
            payload = await client.get_json("ping")
            return str(payload["answer"])

        server.add_tool(ping, name=tool_name)

    return loaded(fake_module(name, [tool_name], register=register, start=start))


class Hosts:
    """The routes of the three mocked hosts, so a test can count what reached each."""

    def __init__(self, router: respx.MockRouter, alpha_status: int) -> None:
        self.alpha = router.get(f"{ALPHA_API}/ping").mock(
            return_value=httpx.Response(alpha_status, json={"error": "body-secret-text"})
        )
        self.beta = router.get(f"{BETA_API}/ping").mock(return_value=httpx.Response(200, json={"answer": "beta-ok"}))
        search_page = json.loads((FIXTURES / "search_results.json").read_text("utf-8"))
        self.sejm = router.get(SEJM_SEARCH).mock(return_value=httpx.Response(200, json=search_page))


@contextmanager
def mocked_hosts(alpha_status: int) -> Iterator[Hosts]:
    """Alpha answers `alpha_status` to everything; Beta and the Sejm answer normally."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield Hosts(router, alpha_status)


@dataclass
class Session:
    """An in-memory MCP client on the real server."""

    client: Client

    async def text(self, name: str, arguments: dict[str, object] | None = None) -> str:
        """Call a tool that must succeed; return the text the agent reads."""
        result = await self.client.call_tool(name, arguments or {})
        assert result.is_error is False, result.content
        return str(result.content[0].text)  # type: ignore[union-attr]

    async def fail(self, name: str, arguments: dict[str, object] | None = None) -> str:
        """Call a tool that must refuse; return the text the agent reads."""
        result = await self.client.call_tool(name, arguments or {})
        assert result.is_error is True, result.content
        return str(result.content[0].text)  # type: ignore[union-attr]

    async def sejm_search_count(self) -> int:
        """A Sejm-backed core tool: how many acts the mocked search page returned."""
        result = await self.client.call_tool("search_legal_acts", {"publisher": "DU", "year": 2024, "limit": 2})
        assert result.is_error is False, result.content
        assert result.structured_content is not None
        return int(result.structured_content["data"]["returned_count"])


@asynccontextmanager
async def two_sources() -> AsyncIterator[Session]:
    """The real server with fake modules Alpha and Beta, behind an in-memory client."""
    app = build_app([source("alpha", "Alpha", ALPHA_API), source("beta", "Beta", BETA_API)])
    async with Client(app) as client:
        yield Session(client=client)


def breaker_states() -> dict[str, str]:
    """What `/health` lists under `sources`: the label and breaker state of every registered host."""
    return {item["name"]: item["circuit_state"] for item in server_module._health_state.sources()}


async def trip_alpha(session: Session) -> list[str]:
    """Call Alpha until its breaker opens; return the texts the agent read."""
    texts = []
    for _ in range(12):
        texts.append(await session.fail("alpha_ping"))
        if breaker_states()["Alpha"] == "open":
            break
    return texts


async def test_an_outage_of_one_source_leaves_the_other_source_and_the_sejm_answering() -> None:
    with mocked_hosts(502) as hosts:
        async with two_sources() as session:
            assert set(breaker_states().values()) == {"closed"}
            assert set(breaker_states()) == {"api.sejm.gov.pl", "Alpha", "Beta"}

            texts = await trip_alpha(session)

            states = breaker_states()
            assert states.pop("Alpha") == "open"
            assert set(states.values()) == {"closed"}, "every other host is untouched"
            for text in texts:
                assert "Alpha" in text and OTHER_SOURCES in text and text.endswith("Ponów wywołanie za chwilę.")
                assert "alpha.example.test" not in text, "the source label is named, never its host"
                assert "secret" not in text, "the host's own words never reach the agent"

            sent_to_alpha = hosts.alpha.call_count
            assert await session.text("beta_ping") == "beta-ok"
            assert await session.sejm_search_count() >= 1
            assert hosts.beta.call_count == 1 and hosts.sejm.call_count == 1
            assert hosts.alpha.call_count == sent_to_alpha, "the healthy calls never touched Alpha"


async def test_an_open_breaker_answers_for_the_failing_source_without_sending() -> None:
    with mocked_hosts(502) as hosts:
        async with two_sources() as session:
            await trip_alpha(session)
            sent_to_alpha = hosts.alpha.call_count

            text = await session.fail("alpha_ping")

            assert hosts.alpha.call_count == sent_to_alpha
            assert "bezpiecznik" in text and "Alpha" in text and OTHER_SOURCES in text


@pytest.mark.parametrize("status", [401, 403, 451])
async def test_a_source_that_refuses_the_server_is_unavailable_not_to_be_retried_and_the_others_answer(
    status: int,
) -> None:
    with mocked_hosts(status) as hosts:
        async with two_sources() as session:
            text = await session.fail("alpha_ping")

            # The SDK puts "Error executing tool <name>: " in front of a tool's own message.
            assert text.removeprefix("Error executing tool alpha_ping: ") == (
                f"Źródło Alpha jest niedostępne: odrzuciło zapytanie tego serwera (HTTP {status}). "
                f"{OTHER_SOURCES} {REFUSAL_GUIDANCE}"
            )
            assert "Ponów" not in text
            assert "secret" not in text
            assert hosts.alpha.call_count == 1, "a refusal is terminal: it is not retried"
            assert breaker_states()["Alpha"] == "closed", "a refusal is not an outage, so the breaker stays shut"

            assert await session.text("beta_ping") == "beta-ok"
            assert await session.sejm_search_count() >= 1
