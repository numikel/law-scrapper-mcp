"""Helpers of the judgment-reading tests: a fake SAOS that serves the recorded skeletons.

No SAOS record is invented. The details are the skeleton recordings (`judgment_skeleton_*.json`: the structure
of a real detail, every letter of its text masked) and the pairing searches are the recorded pages
(`search_case_number_pair.json`, `search_case_number_lone_sentence.json`). A shape no recording has (two reasons
records, a reasons record of another court, a page cut short) is derived from a recorded record by changing
the one key the test is about, as `saos_testing.derived_list` does. The words of a text are derived inside the
test that needs them and are plain placeholders.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import respx
from mcp import Client
from saos_testing import DETAIL_URL, SEARCH_URL, Session, loaded, make_saos_client, page_body, recording

from law_scrapper_mcp.client.egress import EgressHub
from law_scrapper_mcp.services.document_store import DocumentStore
from law_scrapper_mcp_saos.client import SaosClient
from law_scrapper_mcp_saos.service import SaosReadingService

SENTENCE_ID = 554697
REASONS_ID = 554698
NESTED_ID = 12510
SUPREME_ID = 245360
LONE_ID = 197824
PAIR_NUMBER = "VIII U 2924/25"
LONE_NUMBER = "VIII U 1302/15"
EXTRA_REASONS_ID = 9_100_001

SKELETONS = {
    SENTENCE_ID: "judgment_skeleton_sentence_split.json",
    REASONS_ID: "judgment_skeleton_reasons.json",
    NESTED_ID: "judgment_skeleton_sentence_nested.json",
    SUPREME_ID: "judgment_skeleton_supreme.json",
}


def skeleton(source_id: int) -> dict[str, Any]:
    """The recorded skeleton of a detail, as SAOS answers (a deep copy: a test may change it)."""
    return copy.deepcopy(recording(SKELETONS[source_id]))


def pair_page() -> dict[str, Any]:
    """The recorded page for `VIII U 2924/25`: the sentence and its reasons record."""
    return copy.deepcopy(recording("search_case_number_pair.json"))


def lone_page() -> dict[str, Any]:
    """The recorded page for `VIII U 1302/15`: one sentence, no reasons record."""
    return copy.deepcopy(recording("search_case_number_lone_sentence.json"))


def lone_detail() -> dict[str, Any]:
    """A detail for the lone sentence, derived from the sentence skeleton.

    No detail of that record was recorded. The body keeps the shape and the text of the sentence skeleton (no
    reasoning heading) and takes the id, signature and division from the recorded search item.
    """
    item = lone_page()["items"][0]
    body = skeleton(SENTENCE_ID)
    division_type = body["data"]["division"].get("type")
    body["data"].update(
        id=item["id"],
        courtCases=item["courtCases"],
        judgmentDate=item["judgmentDate"],
        division={**item["division"], **({"type": division_type} if division_type else {})},
    )
    return body


def reasons_item(page: dict[str, Any]) -> dict[str, Any]:
    """The reasons record of a recorded pair page."""
    return next(item for item in page["items"] if item["judgmentType"] == "REASONS")


def with_second_reasons(page: dict[str, Any]) -> dict[str, Any]:
    """The pair page with one more reasons record of the same court and case, under another id."""
    extra = copy.deepcopy(reasons_item(page))
    extra["id"] = EXTRA_REASONS_ID
    page["items"].append(extra)
    page["info"]["totalResults"] = len(page["items"])
    return page


def with_reasons_of_another_court(page: dict[str, Any]) -> dict[str, Any]:
    """The pair page whose reasons record belongs to another court (same case number, other court id and name)."""
    court = reasons_item(page)["division"]["court"]
    court["id"] = court["id"] + 1
    court["name"] = "Sąd Okręgowy w innym mieście"
    return page


def cut_short(page: dict[str, Any], *, total: int, drop_reasons: bool) -> dict[str, Any]:
    """The pair page as SAOS gives it when more records match than one page holds."""
    if drop_reasons:
        page["items"] = [item for item in page["items"] if item["judgmentType"] != "REASONS"]
    page["info"]["totalResults"] = total
    return page


class FakeReading:
    """A respx-backed SAOS for the reading path: details by id, searches by case number.

    `detail_ids` and `search_params` list what was asked, in order; every HTTP request counts, a retried one
    again (the egress client retries a 5xx).
    """

    def __init__(
        self,
        details: dict[int, dict[str, Any]],
        searches: dict[str, dict[str, Any]] | None = None,
        *,
        failing_search: bool = False,
        failing_details: Iterable[int] = (),
        missing_details: Iterable[int] = (),
        refused_search: bool = False,
        refused_details: Iterable[int] = (),
        refusal_status: int = 403,
    ) -> None:
        self.details = details
        self.searches = searches or {}
        self.failing_search = failing_search
        self.failing_details = set(failing_details)
        self.missing_details = set(missing_details)
        self.refused_search = refused_search
        self.refused_details = set(refused_details)
        self.refusal_status = refusal_status
        self.detail_ids: list[int] = []
        self.search_params: list[dict[str, str]] = []

    @property
    def requests(self) -> int:
        return len(self.detail_ids) + len(self.search_params)

    def _detail(self, request: httpx.Request) -> httpx.Response:
        source_id = int(request.url.path.rsplit("/", 1)[-1])
        self.detail_ids.append(source_id)
        if source_id in self.refused_details:
            return httpx.Response(self.refusal_status, text="refused")
        if source_id in self.failing_details:
            return httpx.Response(502, text="upstream failure")
        if source_id in self.missing_details or source_id not in self.details:
            return httpx.Response(404)
        return httpx.Response(200, json=self.details[source_id])

    def _search(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.search_params.append(params)
        if self.refused_search:
            return httpx.Response(self.refusal_status, text="refused")
        if self.failing_search:
            return httpx.Response(502, text="upstream failure")
        page = self.searches.get(params.get("caseNumber", ""))
        return httpx.Response(200, json=page if page is not None else page_body([], 0))

    def install(self, router: respx.MockRouter) -> FakeReading:
        router.get(SEARCH_URL).mock(side_effect=self._search)
        router.get(url__regex=DETAIL_URL).mock(side_effect=self._detail)
        return self


@dataclass
class ReadingHarness:
    """A fake SAOS, the loader in front of it, and the document store the loader writes to."""

    fake: FakeReading
    service: SaosReadingService
    store: DocumentStore
    client: SaosClient
    hub: EgressHub


MakeReading = Callable[..., ReadingHarness]


@asynccontextmanager
async def reading_session(router: respx.MockRouter, fake: FakeReading) -> AsyncIterator[Session]:
    """The server with only the SAOS module and a fake SAOS that serves the skeletons, and a session on top.

    A context manager rather than a fixture, like `saos_testing.saos_session`: the client's task group must be
    entered and left by one task.
    """
    # Imported here: importing the server runs discovery, so a broken descriptor fails a named test.
    from law_scrapper_mcp.server import build_app

    fake.install(router)
    async with Client(build_app([loaded()])) as client:
        yield Session(client=client, tools={tool.name: tool for tool in (await client.list_tools()).tools})


def make_reading_harness(router: respx.MockRouter, fake: FakeReading) -> ReadingHarness:
    """The loader on a real egress client with the module's own pace, over a fake SAOS."""
    fake.install(router)
    client, hub = make_saos_client()
    store = DocumentStore()
    return ReadingHarness(fake=fake, service=SaosReadingService(client, store), store=store, client=client, hub=hub)


def pair_fake(**overrides: Any) -> FakeReading:
    """The usual fake: the pair of 554697 and 554698, the nested sentence, the Supreme Court text and the lone sentence."""
    details = {source_id: skeleton(source_id) for source_id in SKELETONS}
    details[LONE_ID] = lone_detail()
    searches = {PAIR_NUMBER: pair_page(), LONE_NUMBER: lone_page()}
    return FakeReading(details, searches, **overrides)
