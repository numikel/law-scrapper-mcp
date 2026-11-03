"""The north-star flow through the real tool surface, with the module active.

Tools are called through an in-memory MCP client over the real server (lifespan, services, result and
document stores, error mapping, output schemas). Every journal host is mocked by `respx` from the
recordings in `tests/fixtures/edziennik/`; a request to `api.sejm.gov.pl` fails the test.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import respx
from journal_support import KP, PM, Session, journal_session, load_bytes, load_json, synthetic_pdf

pytestmark = pytest.mark.asyncio

ART_ACT = "POL_WOJ_PM/2025/5051"
SCAN_ACT = "POL_WOJ_PM/1999/1447"
CID_ACT = "POL_WOJ_PM/2025/6001"
PASCAL_ACT = "POL_WOJ_KP/2025/6588"
ARTICLE_PDF = synthetic_pdf(
    [
        "Art. 1. Uchwala wchodzi w zycie z dniem podjecia.",
        "Art. 2. Skarbnik wykonuje budzet gminy.",
        "Art. 3. Traci moc uchwala poprzednia.",
    ]
)
CID_PDF = synthetic_pdf(["Art. 1. " + "(cid:12)" * 30, "Art. 2. koniec"])


@dataclass
class Hosts:
    """The mocked journal hosts and the tripwire on the Sejm."""

    router: respx.MockRouter
    pm_search: respx.Route
    kp_search: respx.Route
    sejm: respx.Route


@pytest.fixture
def hosts() -> Iterator[Hosts]:
    detail_camel = load_json("act_detail_camel.json")
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        pm_search = router.get(f"{PM}/search").mock(
            return_value=httpx.Response(200, json=load_json("search_page_camel.json"))
        )
        kp_search = router.get(f"{KP}/search").mock(
            return_value=httpx.Response(200, json=load_json("search_page_pascal.json"))
        )
        router.get(f"{PM}/acts/{ART_ACT}").mock(return_value=httpx.Response(200, json=detail_camel))
        router.get(f"{PM}/acts/{ART_ACT}/text.pdf").mock(return_value=httpx.Response(200, content=ARTICLE_PDF))
        router.get(f"{PM}/acts/{ART_ACT}/references").mock(return_value=httpx.Response(200, json={}))
        router.get(f"{PM}/acts/POL_WOJ_PM/2025/5049/references").mock(
            return_value=httpx.Response(200, json=load_json("references_relation.json"))
        )
        router.get(f"{PM}/acts/{SCAN_ACT}").mock(return_value=httpx.Response(200, json=detail_camel))
        router.get(f"{PM}/acts/{SCAN_ACT}/text.pdf").mock(
            return_value=httpx.Response(200, content=load_bytes("scan_no_text.pdf"))
        )
        router.get(f"{PM}/acts/{CID_ACT}").mock(return_value=httpx.Response(200, json=detail_camel))
        router.get(f"{PM}/acts/{CID_ACT}/text.pdf").mock(return_value=httpx.Response(200, content=CID_PDF))
        router.get(f"{KP}/acts/{PASCAL_ACT}").mock(
            return_value=httpx.Response(200, json=load_json("act_detail_pascal.json"))
        )
        router.get(f"{PM}/acts/POL_WOJ_PM/2025/999999").mock(return_value=httpx.Response(204))
        sejm = router.route(host="api.sejm.gov.pl").mock(return_value=httpx.Response(500))
        yield Hosts(router=router, pm_search=pm_search, kp_search=kp_search, sejm=sejm)
        assert sejm.call_count == 0, "a journal code reached api.sejm.gov.pl"


def with_session(test: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
    """Run a test inside `journal_session()`, passing it as the `session` argument.

    The `session` parameter is hidden from pytest, so it is not looked up as a fixture. The `hosts`
    fixture is required of every wrapped test, whether or not the test names it: without the mocked
    hosts a call would leave the process, so a test cannot forget it.
    """

    signature = inspect.signature(test)
    declared = set(signature.parameters)

    @functools.wraps(test)
    async def wrapper(*args: Any, **kwargs: Any) -> None:
        if "hosts" not in declared:
            kwargs.pop("hosts")
        async with journal_session() as session:
            await test(*args, session=session, **kwargs)

    parameters = [parameter for name, parameter in signature.parameters.items() if name != "session"]
    if "hosts" not in declared:
        parameters.append(inspect.Parameter("hosts", inspect.Parameter.KEYWORD_ONLY, annotation=Hosts))
    wrapper.__signature__ = signature.replace(parameters=parameters)  # type: ignore[attr-defined]
    return wrapper


# --- the flow ----------------------------------------------------------------------------------


@with_session
async def test_browse_filter_load_read_and_search_one_act(session: Session, hosts: Hosts) -> None:
    items = load_json("search_page_camel.json")["items"]
    wanted_type = items[0]["type"]

    page = await session.call("browse_acts", {"publisher": "POL_WOJ_PM", "year": 2025, "limit": 5})
    assert page["returned_count"] == len(items)
    assert page["total_count"] == load_json("search_page_camel.json")["totalCount"]
    assert all(result["eli"].startswith("POL_WOJ_PM/2025/") for result in page["results"])
    assert page["result_set_id"]

    filtered = await session.call(
        "filter_results", {"result_set_id": page["result_set_id"], "type_equals": wanted_type}
    )
    assert filtered["filtered_count"] == sum(1 for item in items if item["type"] == wanted_type)

    details = await session.call("get_act_details", {"eli": ART_ACT, "load_content": True})
    assert details["eli"] == ART_ACT
    assert details["content_status"] == "loaded"
    assert details["toc"] == []

    toc = await session.call("read_act_content", {"eli": ART_ACT})
    assert [entry["title"] for entry in toc["toc"]][0].startswith("Art. 1.")
    article = await session.call("read_act_content", {"eli": ART_ACT, "section": toc["toc"][1]["id"]})
    assert "Skarbnik" in article["content"]

    found = await session.call("search_in_act", {"eli": ART_ACT, "query": "budzet"})
    assert found["total_matches"] == 1

    assert hosts.pm_search.call_count == 1
    assert hosts.sejm.call_count == 0


@with_session
async def test_search_legal_acts_returns_a_stored_set_with_iso_dates(session: Session, hosts: Hosts) -> None:
    page = await session.call(
        "search_legal_acts", {"publisher": "POL_WOJ_PM", "year": 2025, "title": "budżet", "limit": 5}
    )

    request = hosts.pm_search.calls.last.request
    assert dict(request.url.params) == {"limit": "5", "year": "2025", "title": "budżet"}
    first = page["results"][0]
    assert first["promulgation_date"] == load_json("search_page_camel.json")["items"][0]["announcementDate"][:10]
    assert first["effective_date"] is None
    assert page["result_set_id"]


@with_session
async def test_a_pascal_journal_works_through_the_same_tools(session: Session) -> None:
    page = await session.call("browse_acts", {"publisher": "POL_WOJ_KP", "year": 2025, "limit": 2})
    details = await session.call("get_act_details", {"eli": PASCAL_ACT})

    assert page["returned_count"] == 2
    assert details["eli"] == PASCAL_ACT
    assert details["keywords"] == ["porozumienia", "transport lokalny"]


@with_session
async def test_track_legal_changes_asks_for_the_publication_window(session: Session, hosts: Hosts) -> None:
    changes = await session.call(
        "track_legal_changes",
        {"publisher": "POL_WOJ_PM", "date_from": "2025-12-01", "date_to": "2025-12-31", "limit": 5},
    )

    request = hosts.pm_search.calls.last.request
    assert dict(request.url.params) == {"limit": "5", "pubDateFrom": "2025-12-01", "pubDateTo": "2025-12-31"}
    assert changes["publisher"] == "POL_WOJ_PM"
    assert changes["date_range"] == "2025-12-01 to 2025-12-31"
    assert changes["changes"]
    assert changes["result_set_id"]


@with_session
async def test_relations_come_back_under_the_journals_own_keys_or_empty(session: Session) -> None:
    raw = load_json("references_relation.json")

    with_relations = await session.call("analyze_act_relationships", {"eli": "POL_WOJ_PM/2025/5049"})
    without = await session.call("analyze_act_relationships", {"eli": ART_ACT})

    assert list(with_relations["relationships"]) == list(raw)
    assert with_relations["total_count"] == 1
    assert without["relationships"] == {}
    assert without["total_count"] == 0


@with_session
async def test_two_journal_acts_can_be_compared(session: Session) -> None:
    comparison = await session.call("compare_acts", {"eli_a": ART_ACT, "eli_b": PASCAL_ACT})

    assert comparison["eli_a"] == ART_ACT
    assert comparison["eli_b"] == PASCAL_ACT
    assert comparison["differences"]


# --- content outcomes --------------------------------------------------------------------------


@with_session
async def test_a_scanned_pdf_is_unavailable_and_the_hint_links_the_journal_pdf(session: Session) -> None:
    result = await session.client.call_tool("get_act_details", {"eli": SCAN_ACT, "load_content": True})

    assert result.is_error is False
    payload = result.structured_content
    assert payload is not None
    assert payload["data"]["content_status"] == "unavailable"
    messages = " ".join(hint["message"] for hint in payload["hints"])
    assert f"{PM}/acts/{SCAN_ACT}/text.pdf" in messages
    assert "api.sejm.gov.pl" not in messages


@with_session
async def test_a_garbled_text_layer_still_loads(session: Session, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="law_scrapper_mcp.services.act_service"):
        details = await session.call("get_act_details", {"eli": CID_ACT, "load_content": True})

    assert details["content_status"] == "loaded"
    assert any("garbled" in record.getMessage() and CID_ACT in record.getMessage() for record in caplog.records)
    found = await session.call("search_in_act", {"eli": CID_ACT, "query": "koniec"})
    assert found["total_matches"] == 1


@with_session
async def test_a_missing_act_is_a_not_found_error_not_a_crash(session: Session) -> None:
    text = await session.fail("get_act_details", {"eli": "POL_WOJ_PM/2025/999999"})

    assert "POL_WOJ_PM/2025/999999" in text


# --- refusals ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments", "expected"),
    [
        pytest.param(
            "search_legal_acts",
            {"publisher": "POL_WOJ_PM", "year": 2025, "in_force": True},
            "in_force",
            id="in_force",
        ),
        pytest.param(
            "search_legal_acts",
            {"publisher": "POL_WOJ_PM", "year": 2025, "date_from": "2025-01-01"},
            "date_from",
            id="date_from",
        ),
        pytest.param(
            "search_legal_acts",
            {"publisher": "POL_WOJ_PM", "year": 2025, "keywords": ["a", "b"]},
            "jedno słowo kluczowe",
            id="two keywords",
        ),
        pytest.param(
            "search_legal_acts",
            {"publisher": "POL_WOJ_PM", "year": 2025, "limit": 101},
            "nie może przekraczać 100",
            id="limit 101",
        ),
        pytest.param(
            "track_legal_changes",
            {"publisher": "POL_WOJ_PM", "date_from": "2025-01-01", "keywords": ["a", "b"]},
            "jedno słowo kluczowe",
            id="track with two keywords",
        ),
        pytest.param("browse_acts", {"publisher": "POL_WOJ_XX", "year": 2025}, "POL_WOJ_XX", id="unknown code"),
        pytest.param("browse_acts", {"publisher": "POL_WOJ_MZ", "year": 2025}, "wyłączony", id="withheld MZ"),
        pytest.param("get_act_details", {"eli": "POL_WOJ_LS/2025/1"}, "wyłączony", id="withheld LS"),
    ],
)
@with_session
async def test_unsupported_requests_fail_loudly_in_polish(
    session: Session, hosts: Hosts, tool: str, arguments: dict[str, Any], expected: str
) -> None:
    before = len(hosts.router.calls)

    text = await session.fail(tool, arguments)

    assert expected in text
    assert len(hosts.router.calls) == before, "a refusal must not cost a single request"


@with_session
async def test_the_withheld_journals_are_never_contacted(session: Session, hosts: Hosts) -> None:
    for code in ("POL_WOJ_MZ", "POL_WOJ_LS", "POL_WOJ_LD", "POL_WOJ_MP"):
        await session.fail("search_legal_acts", {"publisher": code, "year": 2025})

    assert len(hosts.router.calls) == 0
