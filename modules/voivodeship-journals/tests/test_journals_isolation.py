"""One failing journal host must not touch the others: not DU, not another journal.

Each journal has its own egress client (own breaker, own pacing budget, own cache space), so a 5xx
storm on one host is a local event. The test runs the real server with the Sejm and two journal hosts
mocked by `respx`, and reads the same source list `/health` serves.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
import respx
from journal_support import FIXTURES, PM, Session, journal_session, load_json

from law_scrapper_mcp.client import egress as egress_module
from law_scrapper_mcp.client.egress import EgressClient

pytestmark = pytest.mark.asyncio

DS = "https://edzienniki.duw.pl/api/eli"
SEJM_SEARCH = "https://api.sejm.gov.pl/eli/acts/search"
OTHER_SOURCES = "Pozostałe źródła nie są objęte tym błędem"


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """The retry loop sleeps between attempts; a storm test must not sit those waits out."""

    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(egress_module, "_delay", instant)


@pytest.fixture
def storm() -> Iterator[dict[str, respx.Route]]:
    """PM answers 503 to everything; DS and the Sejm answer normally."""
    sejm_page = FIXTURES.parent / "search_results.json"
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        routes = {
            "pm": router.route(host="edziennik.gdansk.uw.gov.pl").mock(return_value=httpx.Response(503)),
            "ds": router.get(f"{DS}/search").mock(
                return_value=httpx.Response(200, json=load_json("search_page_camel.json"))
            ),
            "sejm": router.get(SEJM_SEARCH).mock(
                return_value=httpx.Response(200, json=json.loads(sejm_page.read_text(encoding="utf-8")))
            ),
        }
        yield routes


def breaker_states() -> dict[str, str]:
    """What `/health` lists under `sources`: name and breaker state of every registered host."""
    from law_scrapper_mcp.server import _health_state

    return {item["name"]: item["circuit_state"] for item in _health_state.sources()}


async def trip_pm(session: Session) -> list[str]:
    """Call PM until its breaker opens; return the texts the agent read."""
    texts = []
    for _ in range(12):
        texts.append(await session.fail("search_legal_acts", {"publisher": "POL_WOJ_PM", "year": 2025}))
        if breaker_states()["POL_WOJ_PM"] == "open":
            break
    return texts


async def test_a_storm_on_one_journal_opens_only_its_own_breaker(storm: dict[str, respx.Route]) -> None:
    async with journal_session() as session:
        assert set(breaker_states().values()) == {"closed"}

        texts = await trip_pm(session)

        states = breaker_states()
        assert states.pop("POL_WOJ_PM") == "open"
        assert set(states.values()) == {"closed"}, "every other host, the Sejm included, is untouched"
        assert "api.sejm.gov.pl" in states
        assert len(states) == 12, "11 other journals and the Sejm"
        assert all("POL_WOJ_PM" in text for text in texts)
        assert all(OTHER_SOURCES in text for text in texts)


async def test_other_journals_and_the_sejm_still_answer_while_one_is_down(storm: dict[str, respx.Route]) -> None:
    async with journal_session() as session:
        await trip_pm(session)
        pm_calls = storm["pm"].call_count

        other_journal = await session.call("search_legal_acts", {"publisher": "POL_WOJ_DS", "year": 2025, "limit": 2})
        sejm = await session.call("search_legal_acts", {"publisher": "DU", "year": 2024, "limit": 2})

        assert other_journal["returned_count"] >= 1
        assert sejm["returned_count"] >= 1
        assert storm["ds"].call_count == 1
        assert storm["sejm"].call_count == 1
        assert storm["pm"].call_count == pm_calls, "the healthy calls never touched the failing host"


async def test_an_open_breaker_stops_traffic_to_that_host_only(storm: dict[str, respx.Route]) -> None:
    async with journal_session() as session:
        await trip_pm(session)
        sent = storm["pm"].call_count

        text = await session.fail("browse_acts", {"publisher": "POL_WOJ_PM", "year": 2025})

        assert storm["pm"].call_count == sent, "an open breaker answers without sending"
        assert "bezpiecznik" in text
        assert OTHER_SOURCES in text


async def test_every_journal_has_its_own_pacing_budget_and_breaker(storm: dict[str, respx.Route]) -> None:
    from law_scrapper_mcp.server import _health_state

    async with journal_session():
        hub = _health_state._hub
        assert hub is not None
        clients: list[EgressClient] = list(hub._clients.values())
        journals = [client for client in clients if client.host.name.startswith("POL_WOJ_")]
        sejm = next(client for client in clients if client.host.name == "api.sejm.gov.pl")

        limiters = {id(client._rate_limiter) for client in clients}
        breakers = {id(client._circuit_breaker) for client in clients}
        assert len(journals) == 12
        assert len(limiters) == len(clients), "no pacing bucket is shared, so PM cannot draw on the Sejm's"
        assert len(breakers) == len(clients)
        assert id(sejm._rate_limiter) not in {id(client._rate_limiter) for client in journals}


async def test_the_failing_host_is_the_one_the_message_names(storm: dict[str, respx.Route]) -> None:
    async with journal_session() as session:
        text = await session.fail("search_legal_acts", {"publisher": "POL_WOJ_PM", "year": 2025})

        assert PM.split("/")[2] not in text, "the host name stays out; the source label is the code"
        assert "POL_WOJ_PM" in text
