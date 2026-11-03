"""`JournalActSource` against the recorded payloads, with every journal host mocked by `respx`."""

from __future__ import annotations

import httpx
import pytest
import respx
from journal_support import KP, MZ, PM, SK, Started, load_bytes, load_json

from law_scrapper_mcp.client.exceptions import (
    ActNotFoundError,
    ApiUnavailableError,
    ResponseTooLargeError,
    SejmApiError,
)
from law_scrapper_mcp.services.act_sources import ActSource, SourceSearchQuery

pytestmark = pytest.mark.asyncio


def source_for(started: Started, code: str = "POL_WOJ_PM") -> ActSource:
    source = started.registry.for_publisher(code)
    assert source is not None
    return source


# --- search ------------------------------------------------------------------------------------


@respx.mock
async def test_search_sends_exactly_the_documented_parameters_and_never_the_publisher(started: Started) -> None:
    route = respx.get(f"{PM}/search").mock(return_value=httpx.Response(200, json=load_json("search_page_camel.json")))
    query = SourceSearchQuery(
        year=2025,
        keywords=("drogi",),
        title="budżet",
        act_type="Uchwała",
        pub_date_from="2025-01-01",
        pub_date_to="2025-06-30",
        limit=5,
        offset=10,
    )

    await source_for(started).search("POL_WOJ_PM", query)

    assert dict(route.calls.last.request.url.params) == {
        "limit": "5",
        "offset": "10",
        "year": "2025",
        "title": "budżet",
        "type": "Uchwała",
        "keyword": "drogi",
        "pubDateFrom": "2025-01-01",
        "pubDateTo": "2025-06-30",
    }


@respx.mock
async def test_search_always_sends_limit_and_omits_offset_zero(started: Started) -> None:
    """The hosts return an empty default page, so `limit` is never left out."""
    route = respx.get(f"{PM}/search").mock(return_value=httpx.Response(200, json=load_json("search_page_camel.json")))

    await source_for(started).search("POL_WOJ_PM", SourceSearchQuery(year=2025))

    assert dict(route.calls.last.request.url.params) == {"limit": "20", "year": "2025"}


@respx.mock
async def test_search_maps_a_camel_page_and_reports_the_total(started: Started) -> None:
    page_json = load_json("search_page_camel.json")
    respx.get(f"{PM}/search").mock(return_value=httpx.Response(200, json=page_json))

    page = await source_for(started).search("POL_WOJ_PM", SourceSearchQuery(year=2025, limit=5))

    assert len(page.items) == len(page_json["items"])
    assert page.total_count == page_json["totalCount"]
    assert page.items[0].eli == f"POL_WOJ_PM/2025/{page_json['items'][0]['pos']}"


@respx.mock
async def test_search_reads_a_pascal_host(started: Started) -> None:
    page_json = load_json("search_page_pascal.json")
    respx.get(f"{KP}/search").mock(return_value=httpx.Response(200, json=page_json))

    page = await source_for(started, "POL_WOJ_KP").search("POL_WOJ_KP", SourceSearchQuery(year=2025, limit=2))

    assert [item.pos for item in page.items] == [item["Pos"] for item in page_json["Items"]]
    assert page.total_count == page_json["TotalCount"]
    assert all(item.eli.startswith("POL_WOJ_KP/2025/") for item in page.items)


@respx.mock
async def test_each_code_goes_to_its_own_host_only(started: Started) -> None:
    pm = respx.get(f"{PM}/search").mock(return_value=httpx.Response(200, json=load_json("search_page_camel.json")))
    sk = respx.get(f"{SK}/search").mock(return_value=httpx.Response(200, json=load_json("keyword_single_a.json")))

    await source_for(started, "POL_WOJ_SK").search("POL_WOJ_SK", SourceSearchQuery(year=2025, limit=1))

    assert (pm.call_count, sk.call_count) == (0, 1)


@respx.mock
async def test_track_changes_shape_is_the_publication_window_and_nothing_else(started: Started) -> None:
    route = respx.get(f"{PM}/search").mock(return_value=httpx.Response(200, json=load_json("search_page_camel.json")))

    await source_for(started).search(
        "POL_WOJ_PM", SourceSearchQuery(pub_date_from="2025-12-01", pub_date_to="2025-12-31", limit=20)
    )

    assert dict(route.calls.last.request.url.params) == {
        "limit": "20",
        "pubDateFrom": "2025-12-01",
        "pubDateTo": "2025-12-31",
    }


@respx.mock
async def test_a_search_answer_in_a_wrong_shape_is_loud(started: Started) -> None:
    respx.get(f"{PM}/search").mock(return_value=httpx.Response(200, json=["not", "an", "object"]))

    with pytest.raises(SejmApiError) as excinfo:
        await source_for(started).search("POL_WOJ_PM", SourceSearchQuery(year=2025))

    assert excinfo.value.source == "POL_WOJ_PM"


# --- refusals ----------------------------------------------------------------------------------


@respx.mock
async def test_in_force_is_refused_before_any_request(started: Started) -> None:
    route = respx.route().mock(return_value=httpx.Response(500))

    with pytest.raises(ValueError, match="in_force"):
        await source_for(started).search("POL_WOJ_PM", SourceSearchQuery(year=2025, in_force=True))

    assert route.call_count == 0


@respx.mock
@pytest.mark.parametrize(
    ("query", "named"),
    [
        (SourceSearchQuery(date_from="2025-01-01"), "date_from"),
        (SourceSearchQuery(date_to="2025-01-01"), "date_to"),
        (SourceSearchQuery(date_from="2025-01-01", date_to="2025-02-01"), "date_from i date_to"),
    ],
)
async def test_effective_date_filters_are_refused_naming_the_filter(
    started: Started, query: SourceSearchQuery, named: str
) -> None:
    route = respx.route().mock(return_value=httpx.Response(500))

    with pytest.raises(ValueError, match=named) as excinfo:
        await source_for(started).search("POL_WOJ_PM", query)

    assert "pub_date_from" in str(excinfo.value), "the refusal points at the filter that does work"
    assert route.call_count == 0


@respx.mock
async def test_several_keywords_are_refused_as_the_recorded_probe_demands(started: Started) -> None:
    """The recordings show a joined list is no intersection, so the source does not pass one on."""
    count_a = load_json("keyword_single_a.json")["totalCount"]
    count_b = load_json("keyword_single_b.json")["totalCount"]
    count_pair = load_json("keyword_pair_ab.json")["totalCount"]
    assert count_pair > min(count_a, count_b), "premise: the pair is not an AND"
    route = respx.route().mock(return_value=httpx.Response(500))

    with pytest.raises(ValueError, match="jedno słowo kluczowe") as excinfo:
        await source_for(started).search("POL_WOJ_PM", SourceSearchQuery(keywords=("budżety", "finanse publiczne")))

    assert "POL_WOJ_PM" in str(excinfo.value)
    assert route.call_count == 0


@respx.mock
async def test_blank_keywords_do_not_count(started: Started) -> None:
    route = respx.get(f"{PM}/search").mock(return_value=httpx.Response(200, json=load_json("keyword_single_a.json")))

    await source_for(started).search("POL_WOJ_PM", SourceSearchQuery(keywords=(" ", "drogi", ""), year=2025))

    assert route.calls.last.request.url.params["keyword"] == "drogi"


@respx.mock
@pytest.mark.parametrize("code", ["POL_WOJ_MZ", "POL_WOJ_LS", "POL_WOJ_LD", "POL_WOJ_MP"])
async def test_a_withheld_journal_fails_with_its_reason_and_sends_nothing(started: Started, code: str) -> None:
    route = respx.route().mock(return_value=httpx.Response(200, json={}))
    source = source_for(started, code)

    for call in (
        source.search(code, SourceSearchQuery(year=2025)),
        source.get_details(code, 2025, 1),
        source.get_relationships(code, 2025, 1),
        source.get_pdf(code, 2025, 1, max_bytes=1000),
    ):
        with pytest.raises(ApiUnavailableError) as excinfo:
            await call
        assert excinfo.value.source == code
        assert excinfo.value.status_code == 503
        assert code in str(excinfo.value)
        assert "wyłączony" in str(excinfo.value)

    assert route.call_count == 0


@respx.mock
async def test_an_unserved_code_under_the_prefix_lists_the_served_ones(started: Started) -> None:
    route = respx.route().mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(ValueError, match="POL_WOJ_XX") as excinfo:
        await source_for(started, "POL_WOJ_XX").search("POL_WOJ_XX", SourceSearchQuery(year=2025))

    assert "POL_WOJ_PM" in str(excinfo.value) and "POL_WOJ_SK" in str(excinfo.value)
    assert "POL_WOJ_MZ" not in str(excinfo.value), "a withheld code is not offered as served"
    assert route.call_count == 0


@respx.mock
async def test_the_mazovian_host_is_never_contacted(started: Started) -> None:
    mazovia = respx.route(host="edziennik.mazowieckie.pl").mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(ApiUnavailableError):
        await source_for(started, "POL_WOJ_MZ").get_details("POL_WOJ_MZ", 2025, 1)

    assert mazovia.call_count == 0
    assert MZ.startswith("https://edziennik.mazowieckie.pl")


# --- details, relations, PDF -------------------------------------------------------------------


@respx.mock
async def test_details_come_from_the_act_endpoint_and_never_ask_for_struct(started: Started) -> None:
    detail = respx.get(f"{PM}/acts/POL_WOJ_PM/2025/5051").mock(
        return_value=httpx.Response(200, json=load_json("act_detail_camel.json"))
    )
    struct = respx.get(f"{PM}/acts/POL_WOJ_PM/2025/5051/struct").mock(return_value=httpx.Response(404))

    result = await source_for(started).get_details("POL_WOJ_PM", 2025, 5051)

    assert result.eli == "POL_WOJ_PM/2025/5051"
    assert result.has_pdf is True
    assert detail.call_count == 1
    assert struct.call_count == 0


@respx.mock
async def test_a_missing_act_is_http_204_and_becomes_not_found(started: Started) -> None:
    respx.get(f"{PM}/acts/POL_WOJ_PM/2025/999999").mock(return_value=httpx.Response(204))

    with pytest.raises(ActNotFoundError) as excinfo:
        await source_for(started).get_details("POL_WOJ_PM", 2025, 999999)

    assert "POL_WOJ_PM/2025/999999" in str(excinfo.value)
    assert excinfo.value.source == "POL_WOJ_PM"


@respx.mock
async def test_a_204_for_details_is_not_cached(started: Started) -> None:
    route = respx.get(f"{PM}/acts/POL_WOJ_PM/2025/7").mock(return_value=httpx.Response(204))
    source = source_for(started)

    for _ in range(2):
        with pytest.raises(ActNotFoundError):
            await source.get_details("POL_WOJ_PM", 2025, 7)

    assert route.call_count == 2


@respx.mock
async def test_relations_are_read_from_the_references_endpoint_under_the_journals_keys(started: Started) -> None:
    raw = load_json("references_relation.json")
    respx.get(f"{PM}/acts/POL_WOJ_PM/2025/5049/references").mock(return_value=httpx.Response(200, json=raw))

    result = await source_for(started).get_relationships("POL_WOJ_PM", 2025, 5049)

    assert list(result) == list(raw)
    assert result[next(iter(raw))][0]["act"]["eli"] == "POL_WOJ_PM/2026/405"


@respx.mock
@pytest.mark.parametrize("response", [httpx.Response(200, json={}), httpx.Response(204)], ids=["empty object", "204"])
async def test_an_act_without_relations_gives_an_empty_mapping(started: Started, response: httpx.Response) -> None:
    respx.get(f"{PM}/acts/POL_WOJ_PM/2025/5051/references").mock(return_value=response)

    assert await source_for(started).get_relationships("POL_WOJ_PM", 2025, 5051) == {}


@respx.mock
async def test_relations_in_an_unexpected_shape_are_loud_not_empty(started: Started) -> None:
    respx.get(f"{PM}/acts/POL_WOJ_PM/2025/5051/references").mock(return_value=httpx.Response(200, json=[1, 2]))

    with pytest.raises(SejmApiError) as excinfo:
        await source_for(started).get_relationships("POL_WOJ_PM", 2025, 5051)

    assert excinfo.value.source == "POL_WOJ_PM"


@respx.mock
async def test_the_pdf_is_downloaded_from_the_journal_host(started: Started) -> None:
    body = load_bytes("text_layer.pdf")
    route = respx.get(f"{PM}/acts/POL_WOJ_PM/2009/1/text.pdf").mock(return_value=httpx.Response(200, content=body))

    assert await source_for(started).get_pdf("POL_WOJ_PM", 2009, 1, max_bytes=10_000_000) == body
    assert route.call_count == 1


@respx.mock
async def test_the_size_budget_reaches_the_download(started: Started) -> None:
    respx.get(f"{PM}/acts/POL_WOJ_PM/2009/1/text.pdf").mock(
        return_value=httpx.Response(200, content=load_bytes("text_layer.pdf"))
    )

    with pytest.raises(ResponseTooLargeError):
        await source_for(started).get_pdf("POL_WOJ_PM", 2009, 1, max_bytes=1024)


async def test_the_pdf_url_points_at_the_journal_not_at_the_sejm(started: Started) -> None:
    source = source_for(started)

    assert source.pdf_url("POL_WOJ_PM", 2025, 5051) == f"{PM}/acts/POL_WOJ_PM/2025/5051/text.pdf"
    assert source.pdf_url("POL_WOJ_KP", 2025, 6588).startswith(KP)
    assert "api.sejm.gov.pl" not in source.pdf_url("POL_WOJ_SK", 2025, 1)
    with pytest.raises(ValueError, match="POL_WOJ_XX"):
        source.pdf_url("POL_WOJ_XX", 2025, 1)
