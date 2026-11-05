"""A failing SAOS must not touch the Sejm, a voivodeship journal or the other tools, and the reverse.

The test runs the real server with the SAOS module and the voivodeship-journals module, with the Sejm, SAOS
and one journal mocked by `respx`, and reads the same source list `/health` serves. SAOS has its own egress
client (own breaker, own pacing budget, own cache space), so its outage is a local event.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import respx
from mcp import Client
from saos_testing import FakeSaos, Session, loaded

from law_scrapper_mcp.client import egress as egress_module
from law_scrapper_mcp.client.egress import EgressClient
from law_scrapper_mcp.config import Settings
from law_scrapper_mcp.source_modules.discovery import discover

pytestmark = pytest.mark.asyncio

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
SEJM_SEARCH = "https://api.sejm.gov.pl/eli/acts/search"
JOURNAL = "https://edzienniki.duw.pl/api/eli"
OTHER_SOURCES = "Pozostałe źródła nie są objęte tym błędem"
SEARCH = "search_saos_judgments"


@pytest.fixture(autouse=True)
def modules_installed() -> None:
    pytest.importorskip("law_scrapper_mcp_voivodeship_journals", reason="run `uv sync --extra dev --all-packages`")


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """The retry loop sleeps between attempts; a storm test must not sit those waits out."""

    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(egress_module, "_delay", instant)


class Hosts:
    """The routes of the three mocked hosts, so a test can count what reached each."""

    def __init__(self, router: respx.MockRouter, saos: FakeSaos) -> None:
        self.saos = saos.install(router)
        self.sejm = router.get(SEJM_SEARCH).mock(
            return_value=httpx.Response(200, json=json.loads((FIXTURES / "search_results.json").read_text("utf-8")))
        )
        journal_page = json.loads((FIXTURES / "edziennik" / "search_page_camel.json").read_text("utf-8"))
        self.journal = router.get(f"{JOURNAL}/search").mock(return_value=httpx.Response(200, json=journal_page))


@pytest.fixture
def hosts_up() -> Iterator[Hosts]:
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield Hosts(router, FakeSaos())


@pytest.fixture
def saos_down() -> Iterator[Hosts]:
    """SAOS answers 502 to every search; the Sejm and the journal answer normally."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield Hosts(router, FakeSaos(failing_pages=range(500)))


@pytest.fixture
def sejm_down() -> Iterator[Hosts]:
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        hosts = Hosts(router, FakeSaos())
        hosts.sejm.mock(return_value=httpx.Response(503))
        yield hosts


@asynccontextmanager
async def both_modules() -> AsyncIterator[Session]:
    """The real server with SAOS and the journals active, behind an in-memory client."""
    from law_scrapper_mcp.server import build_app

    discovery = discover(Settings(source_modules=["-*", "saos", "voivodeship-journals"]))
    assert sorted(item.module.name for item in discovery.active) == ["saos", "voivodeship-journals"]
    assert loaded().module is next(item.module for item in discovery.active if item.module.name == "saos")
    async with Client(build_app(discovery.active, inactive=discovery.inactive)) as client:
        yield Session(client=client, tools={tool.name: tool for tool in (await client.list_tools()).tools})


def breaker_states() -> dict[str, str]:
    """What `/health` lists under `sources`: name and breaker state of every registered host."""
    from law_scrapper_mcp.server import _health_state

    return {item["name"]: item["circuit_state"] for item in _health_state.sources()}


async def trip_saos(session: Session) -> list[str]:
    """Search SAOS until its breaker opens; return the texts the agent read."""
    texts = []
    for _ in range(12):
        texts.append(await session.fail(SEARCH, {"phrase": "wypadek"}))
        if breaker_states()["SAOS"] == "open":
            break
    return texts


async def test_a_saos_outage_opens_only_its_own_breaker(saos_down: Hosts) -> None:
    async with both_modules() as session:
        assert set(breaker_states().values()) == {"closed"}

        texts = await trip_saos(session)

        states = breaker_states()
        assert states.pop("SAOS") == "open"
        assert set(states.values()) == {"closed"}, "every other host is untouched"
        assert "api.sejm.gov.pl" in states and "POL_WOJ_PM" in states
        assert all("SAOS" in text and OTHER_SOURCES in text for text in texts)


async def test_the_sejm_and_a_journal_still_answer_while_saos_is_down(saos_down: Hosts) -> None:
    async with both_modules() as session:
        await trip_saos(session)
        saos_calls = len(saos_down.saos.calls)

        sejm = await session.call("search_legal_acts", {"publisher": "DU", "year": 2024, "limit": 2})
        journal = await session.call("search_legal_acts", {"publisher": "POL_WOJ_DS", "year": 2025, "limit": 2})
        metadata = await session.call("calculate_legal_date", {"days": 14, "base_date": "2026-01-01"})

        assert sejm["returned_count"] >= 1 and journal["returned_count"] >= 1
        assert metadata["calculated_date"] == "2026-01-15"
        assert saos_down.sejm.call_count == 1 and saos_down.journal.call_count == 1
        assert len(saos_down.saos.calls) == saos_calls, "the healthy calls never touched SAOS"


async def test_a_stored_act_set_keeps_working_while_saos_is_down(saos_down: Hosts) -> None:
    async with both_modules() as session:
        found = await session.call("search_legal_acts", {"publisher": "DU", "year": 2024, "limit": 2})
        await trip_saos(session)

        narrowed = await session.call("filter_results", {"result_set_id": found["result_set_id"], "pattern": ".*"})

        assert narrowed["document_kind"] == "act"
        assert narrowed["filtered_count"] >= 1


async def test_an_open_breaker_stops_traffic_to_saos_only(saos_down: Hosts) -> None:
    async with both_modules() as session:
        await trip_saos(session)
        sent = len(saos_down.saos.calls)

        text = await session.fail(SEARCH, {"phrase": "mobbing"})
        details = await session.fail("get_saos_judgment_details", {"source_id": "554697"})

        assert len(saos_down.saos.calls) == sent and not saos_down.saos.detail_urls
        assert "bezpiecznik" in text and "bezpiecznik" in details
        assert OTHER_SOURCES in text and OTHER_SOURCES in details


async def test_a_sejm_outage_does_not_stop_saos(sejm_down: Hosts) -> None:
    async with both_modules() as session:
        for _ in range(12):
            await session.fail("search_legal_acts", {"publisher": "DU", "year": 2024})
            if breaker_states()["api.sejm.gov.pl"] == "open":
                break

        found = await session.call(SEARCH, {"phrase": "wypadek", "limit": 3})

        assert breaker_states()["api.sejm.gov.pl"] == "open"
        assert breaker_states()["SAOS"] == "closed"
        assert found["returned_count"] == 3


async def test_saos_has_its_own_pacing_budget_and_breaker(hosts_up: Hosts) -> None:
    from law_scrapper_mcp.server import _health_state

    async with both_modules():
        hub = _health_state._hub
        assert hub is not None
        clients: list[EgressClient] = list(hub._clients.values())
        saos = next(client for client in clients if client.host.name == "SAOS")
        sejm = next(client for client in clients if client.host.name == "api.sejm.gov.pl")

        assert len({id(client._rate_limiter) for client in clients}) == len(clients), "no pacing bucket is shared"
        assert len({id(client._circuit_breaker) for client in clients}) == len(clients)
        assert id(saos._rate_limiter) != id(sejm._rate_limiter)


async def test_the_failing_source_is_named_by_its_label_and_not_by_its_host(saos_down: Hosts) -> None:
    async with both_modules() as session:
        text = await session.fail(SEARCH, {"phrase": "wypadek"})

        assert "SAOS" in text
        assert "saos.org.pl" not in text, "the host name stays out; the source label is SAOS"
