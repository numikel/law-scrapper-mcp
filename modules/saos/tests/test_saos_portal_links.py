"""`include_portal_links`: the first results get a detail request each, inside a six-request budget."""

from __future__ import annotations

import pytest
import respx
from saos_testing import FakeSaos, HarnessFactory, connected, corpus, detail_body, portal_url_for

from law_scrapper_mcp_saos.service import PORTAL_LINK_CAP, REQUEST_BUDGET, TOOL_DETAILS

pytestmark = pytest.mark.asyncio

CORPUS = corpus()


def _ids(start: int, stop: int) -> list[int]:
    return [item["id"] for item in CORPUS[start:stop]]


async def test_the_constants_are_the_ones_the_tool_documents() -> None:
    assert (PORTAL_LINK_CAP, REQUEST_BUDGET) == (5, 6)


class TestCap:
    async def test_an_aligned_page_gets_links_for_five_results_and_costs_six_requests(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness()

        response = await harness.service.search(limit=20, include_portal_links=True)

        links = response.data.portal_links
        assert harness.fake.pages == [0]
        assert harness.fake.detail_ids == _ids(0, 5)
        assert harness.fake.requests == 6
        assert links is not None
        assert (links.requested, links.resolved, links.failed, links.without_link) == (5, 5, 0, 0)
        assert links.skipped_for_budget == 15
        assert links.stopped_after_outage is False
        urls = [r.portal_url for r in response.data.results]
        assert urls[:5] == [portal_url_for(i) for i in _ids(0, 5)]
        assert urls[5:] == [None] * 15

    async def test_a_misaligned_page_gets_four_because_the_search_took_two_requests(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness()

        response = await harness.service.search(limit=10, offset=3, include_portal_links=True)

        links = response.data.portal_links
        assert harness.fake.pages == [0, 1]
        assert harness.fake.detail_ids == _ids(3, 7)
        assert harness.fake.requests == 6
        assert links is not None and (links.requested, links.skipped_for_budget) == (4, 6)

    @pytest.mark.parametrize("limit", [1, 2, 3, 4])
    async def test_a_page_shorter_than_the_cap_asks_for_every_result(
        self, make_harness: HarnessFactory, limit: int
    ) -> None:
        harness = make_harness()

        response = await harness.service.search(limit=limit, include_portal_links=True)

        links = response.data.portal_links
        assert links is not None and (links.requested, links.resolved, links.skipped_for_budget) == (limit, limit, 0)
        assert harness.fake.requests == 1 + limit

    async def test_an_empty_page_asks_for_nothing(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos([]))

        response = await harness.service.search(include_portal_links=True)

        links = response.data.portal_links
        assert links is not None and (links.requested, links.resolved, links.failed) == (0, 0, 0)
        assert harness.fake.detail_ids == []

    async def test_without_the_flag_there_is_no_detail_request_and_no_summary_of_links(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness()

        response = await harness.service.search(limit=5)

        assert harness.fake.detail_ids == []
        assert response.data.portal_links is None
        assert all(r.portal_url is None for r in response.data.results)


LIMITS = (1, 2, 3, 5, 7, 10, 20, 100)
OFFSETS = (0, 1, 2, 3, 5, 7, 10, 13, 20, 37)


class TestRequestBudget:
    @pytest.mark.parametrize("limit", LIMITS)
    @pytest.mark.parametrize("offset", OFFSETS)
    async def test_a_call_never_sends_more_than_six_requests(
        self, make_harness: HarnessFactory, limit: int, offset: int
    ) -> None:
        harness = make_harness()

        response = await harness.service.search(limit=limit, offset=offset, include_portal_links=True)

        returned = response.data.returned_count
        search_requests = len(harness.fake.calls)
        assert search_requests in (1, 2)
        assert (search_requests == 1) or (offset % limit != 0)
        assert harness.fake.requests <= REQUEST_BUDGET
        assert len(harness.fake.detail_urls) == min(PORTAL_LINK_CAP, REQUEST_BUDGET - search_requests, returned)
        assert len(set(harness.fake.detail_ids)) == len(harness.fake.detail_ids), "no judgment is asked for twice"


class TestFailures:
    async def test_one_failing_detail_leaves_that_result_without_a_link_and_the_rest_keep_theirs(
        self, make_harness: HarnessFactory
    ) -> None:
        ids = _ids(0, 5)
        harness = make_harness(FakeSaos(missing_details={ids[2]}))

        response = await harness.service.search(limit=20, include_portal_links=True)

        links = response.data.portal_links
        urls = [r.portal_url for r in response.data.results[:5]]
        assert links is not None
        assert (links.requested, links.resolved, links.failed, links.stopped_after_outage) == (5, 4, 1, False)
        assert urls == [portal_url_for(i) if i != ids[2] else None for i in ids]
        assert harness.fake.detail_ids == ids, "a missing judgment does not stop the others"
        assert response.data.returned_count == 20

    async def test_the_first_outage_stops_further_detail_requests_and_the_search_result_is_still_returned(
        self, make_harness: HarnessFactory
    ) -> None:
        ids = _ids(0, 5)
        harness = make_harness(FakeSaos(failing_details={ids[1]}))

        response = await harness.service.search(limit=20, include_portal_links=True)

        links = response.data.portal_links
        assert links is not None
        assert (links.requested, links.resolved, links.failed, links.stopped_after_outage) == (5, 1, 4, True)
        assert ids[2] not in harness.fake.detail_ids and ids[3] not in harness.fake.detail_ids
        assert set(harness.fake.detail_ids) == {ids[0], ids[1]}
        assert response.data.returned_count == 20
        assert response.data.results[0].portal_url == portal_url_for(ids[0])
        assert all(r.portal_url is None for r in response.data.results[1:])

    async def test_an_address_that_is_not_http_or_https_counts_as_no_link(self, make_harness: HarnessFactory) -> None:
        item = CORPUS[1]
        harness = make_harness(FakeSaos(details={item["id"]: detail_body(item, portal_url="javascript:alert(1)")}))

        response = await harness.service.search(limit=3, include_portal_links=True)

        links = response.data.portal_links
        assert links is not None and (links.resolved, links.without_link, links.failed) == (2, 1, 0)
        assert response.data.results[1].portal_url is None

    async def test_a_detail_without_an_address_counts_as_no_link(self, make_harness: HarnessFactory) -> None:
        item = CORPUS[0]
        body = detail_body(item)
        body["data"]["source"] = {}
        harness = make_harness(FakeSaos(details={item["id"]: body}))

        response = await harness.service.search(limit=2, include_portal_links=True)

        links = response.data.portal_links
        assert links is not None and (links.resolved, links.without_link) == (1, 1)


class TestStoredSet:
    async def test_the_stored_set_carries_the_links(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        response = await harness.service.search(limit=8, include_portal_links=True)

        stored = await harness.store.get(response.data.result_set_id or "")
        assert stored is not None
        urls = [j.portal_url for j in stored.results]  # type: ignore[union-attr]
        assert urls == [r.portal_url for r in response.data.results]
        assert sum(url is not None for url in urls) == 5

    async def test_the_summary_says_that_links_were_asked_for(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        plain = await harness.service.search(limit=2)
        linked = await harness.service.search(limit=2, include_portal_links=True)

        assert "portal_links" not in plain.data.query_summary
        assert "portal_links=pierwsze 5" in linked.data.query_summary

    async def test_details_are_not_cached_but_the_search_page_is(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        await harness.service.search(limit=3, include_portal_links=True)
        await harness.service.search(limit=3, include_portal_links=True)

        assert harness.fake.pages == [0]
        assert harness.fake.detail_ids == _ids(0, 3) * 2


class TestFlag:
    @pytest.mark.parametrize("value", [True, "true", "TRUE", " 1 ", "yes", "tak"])
    async def test_true_values(self, make_harness: HarnessFactory, value: bool | str) -> None:
        harness = make_harness()

        response = await harness.service.search(limit=2, include_portal_links=value)

        assert response.data.portal_links is not None

    @pytest.mark.parametrize("value", [False, None, "false", "0", "no", "nie", ""])
    async def test_false_values(self, make_harness: HarnessFactory, value: bool | str | None) -> None:
        harness = make_harness()

        response = await harness.service.search(limit=2, include_portal_links=value)

        assert response.data.portal_links is None
        assert harness.fake.detail_ids == []

    async def test_anything_else_is_refused_before_any_request(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        with pytest.raises(ValueError, match="include_portal_links"):
            await harness.service.search(include_portal_links="maybe")

        assert harness.fake.requests == 0


class TestHints:
    async def test_without_the_flag_a_hint_names_the_details_tool_for_the_first_result(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness()

        response = await harness.service.search(limit=3)

        hint = next(h for h in response.hints if h.tool == TOOL_DETAILS)
        assert hint.parameters == {"source_id": str(CORPUS[0]["id"])}
        assert "include_portal_links=true" in hint.message

    async def test_with_the_flag_the_hint_points_at_the_first_result_beyond_the_cap(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness()

        response = await harness.service.search(limit=8, include_portal_links=True)

        hint = next(h for h in response.hints if h.tool == TOOL_DETAILS)
        assert hint.parameters == {"source_id": str(CORPUS[5]["id"])}

    async def test_when_every_result_has_its_link_there_is_no_such_hint(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()

        response = await harness.service.search(limit=3, include_portal_links=True)

        assert all(h.tool != TOOL_DETAILS for h in response.hints)

    async def test_after_failures_the_hint_offers_the_details_tool_for_the_unresolved(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness(FakeSaos(missing_details={CORPUS[0]["id"]}))

        response = await harness.service.search(limit=3, include_portal_links=True)

        hint = next(h for h in response.hints if h.tool == TOOL_DETAILS)
        assert hint.parameters == {"source_id": str(CORPUS[0]["id"])}


class TestTool:
    async def test_the_flag_through_the_protocol_with_its_summary_in_the_output(
        self, saos_router: respx.MockRouter
    ) -> None:
        async with connected(saos_router) as client:
            tool = next(t for t in (await client.list_tools()).tools if t.name == "search_saos_judgments")
            result = await client.call_tool("search_saos_judgments", {"limit": 7, "include_portal_links": True})

        import jsonschema

        assert result.is_error is False
        payload = result.structured_content
        assert payload is not None
        jsonschema.validate(payload, tool.output_schema)
        links = payload["data"]["portal_links"]
        assert links == {
            "requested": 5,
            "resolved": 5,
            "without_link": 0,
            "failed": 0,
            "stopped_after_outage": False,
            "skipped_for_budget": 2,
        }
        assert payload["data"]["results"][0]["portal_url"] == portal_url_for(CORPUS[0]["id"])

    async def test_the_stored_links_survive_filter_results(self, saos_router: respx.MockRouter) -> None:
        async with connected(saos_router) as client:
            search = await client.call_tool("search_saos_judgments", {"limit": 10, "include_portal_links": "true"})
            result_set_id = (search.structured_content or {})["data"]["result_set_id"]
            narrowed = await client.call_tool("filter_results", {"result_set_id": result_set_id, "sort_by": "title"})

        judgments = (narrowed.structured_content or {})["data"]["judgments"]
        assert sum(j["portal_url"] is not None for j in judgments) == 5
