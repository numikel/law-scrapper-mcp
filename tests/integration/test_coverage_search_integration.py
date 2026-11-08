"""Coverage of DU and MP through the real tools, over the real app, with the Sejm mocked from its recordings.

The core runs without any source module here, so only the Sejm providers exist. The registry clock is fixed
(the app's registry would read the wall clock, and the recordings are from 2026-11-08): the newest DU act
is from 2026-11-05, so 2026-11-09 is inside the 30-day window.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from mcp_helpers import parse_tool_result

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SEJM = Path(__file__).parents[1] / "fixtures" / "sejm"
ACTS_URL = "https://api.sejm.gov.pl/eli/acts"
SEARCH_URL = "https://api.sejm.gov.pl/eli/acts/search"
TODAY = date(2026, 11, 9)


def load(name: str) -> Any:
    return json.loads((SEJM / name).read_text(encoding="utf-8"))


@pytest.fixture
def recorded_sejm() -> Iterator[respx.MockRouter]:
    """The publisher list and the newest-year page of DU and MP as recorded, plus a catch-all search page."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        router.get(ACTS_URL).mock(return_value=httpx.Response(200, json=load("publishers_live.json")))
        for code, name in (("DU", "du"), ("MP", "mp")):
            router.get(SEARCH_URL, params={"publisher": code, "year": "2026", "limit": "1"}).mock(
                return_value=httpx.Response(200, json=load(f"search_2026_limit1_{name}.json"))
            )
        # The agent's own queries (a wider page than the one-act measurement): any recorded page will do.
        router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=load("search_2026_limit1_du.json")))
        yield router


@pytest.fixture
async def client(recorded_sejm: respx.MockRouter, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    from mcp import Client

    from law_scrapper_mcp import server
    from law_scrapper_mcp.services.coverage import CoverageRegistry

    monkeypatch.setattr(server, "CoverageRegistry", lambda: CoverageRegistry(today=lambda: TODAY))
    async with Client(server.app) as session:
        yield session


def hint_messages(payload: dict[str, Any]) -> list[str]:
    return [hint["message"] for hint in payload["hints"]]


def coverage_hints(payload: dict[str, Any]) -> list[str]:
    return [m for m in hint_messages(payload) if "Najnowsze ogłoszenie" in m or "nie ma rocznika" in m]


async def test_du_in_the_current_year_has_no_coverage_hint(client: Any) -> None:
    result = await client.call_tool("search_legal_acts", {"publisher": "DU", "year": 2026})

    assert coverage_hints(parse_tool_result(result)) == []


async def test_du_in_a_year_it_never_had_names_the_missing_year(client: Any) -> None:
    result = await client.call_tool("search_legal_acts", {"publisher": "DU", "year": 1900})

    hints = coverage_hints(parse_tool_result(result))
    assert len(hints) == 1
    assert "Źródło „Dziennik Ustaw” nie ma rocznika 1900" in hints[0]
    assert "1918–1939, 1944–2026" in hints[0], "the sentence lists the years the Sejm reported, in ranges"


async def test_mp_in_a_year_it_never_had_names_the_missing_year_too(client: Any) -> None:
    result = await client.call_tool("browse_acts", {"publisher": "MP", "year": 1900})

    hints = coverage_hints(parse_tool_result(result))
    assert len(hints) == 1
    assert "Źródło „Monitor Polski” nie ma rocznika 1900" in hints[0]


async def test_a_query_reaching_a_month_past_the_newest_du_act_gets_the_freshness_hint(client: Any) -> None:
    result = await client.call_tool("search_legal_acts", {"publisher": "DU", "pub_date_to": "2026-12-20"})

    hints = coverage_hints(parse_tool_result(result))
    assert len(hints) == 1
    assert "2026-11-05 (2026/1465)" in hints[0]


async def test_system_metadata_lists_du_and_mp_as_measured(client: Any) -> None:
    result = await client.call_tool("get_system_metadata", {"category": "coverage"})

    entries = parse_tool_result(result)["data"]["metadata"]["coverage"]
    by_segment = {entry["segment"]: entry for entry in entries}
    assert set(by_segment) == {"DU", "MP"}
    for code, ref in (("DU", "2026/1465"), ("MP", "2026/1100")):
        entry = by_segment[code]
        assert (entry["source"], entry["origin"], entry["newest"], entry["newest_ref"]) == (
            "sejm",
            "measured",
            "2026-11-05",
            ref,
        )
        assert entry["measured_on"] == TODAY.isoformat()
        assert entry["years"][-1] == 2026
