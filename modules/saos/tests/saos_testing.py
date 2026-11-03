"""Helpers of the SAOS module tests.

No SAOS record is invented here. Pages are cut from the redacted recordings in `tests/fixtures/saos/`
(see `PROVENANCE.md` there), and the fake server only does what the real one was observed to do:
zero-based `pageNumber`, `pageSize` 1..100, `totalResults` on every page, an empty `items` list past the
last page (recorded in `search_out_of_range_page.json`).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import respx

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.egress import CallerIdentity, EgressClient, EgressDefaults, EgressHost, EgressHub
from law_scrapper_mcp.services.result_store import ResultStore
from law_scrapper_mcp_saos.client import SaosClient
from law_scrapper_mcp_saos.service import SaosSearchService
from law_scrapper_mcp_saos.state import BASE_URL, HOST_LABEL

RECORDINGS = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "saos"
SEARCH_URL = f"{BASE_URL}/search/judgments"
TODAY = date(2026, 11, 3)
IDENTITY = CallerIdentity("law-scrapper-mcp-saos", "0.1.0")

# Recordings whose `items` form the fake server's list. Every one is a search page of the real service.
CORPUS_FILES = (
    "search_labour_division_viii.json",
    "search_common_newest_unbounded.json",
    "search_common_newest_le_today.json",
    "search_supreme_newest_le_today.json",
    "search_newest_constitutional_tribunal.json",
    "search_newest_national_appeal_chamber.json",
    "search_case_number_pair.json",
    "search_viii_sentence_all_years.json",
    "search_viii_reasons_all_years.json",
    "search_by_law_journal_entry.json",
    *(f"search_viii_{kind}_{year}.json" for year in (2013, 2016, 2019, 2022, 2025) for kind in ("sentence", "reasons")),
)


def recording(name: str) -> dict[str, Any]:
    """One recorded file, parsed."""
    data: dict[str, Any] = json.loads((RECORDINGS / name).read_text(encoding="utf-8"))
    return data


def recorded_items(*names: str) -> list[dict[str, Any]]:
    """The `items` of the named recordings, first occurrence of each id only."""
    seen: set[int] = set()
    items: list[dict[str, Any]] = []
    for name in names:
        for item in recording(name)["items"]:
            if item["id"] not in seen:
                seen.add(item["id"])
                items.append(item)
    return items


def corpus() -> list[dict[str, Any]]:
    """The records the fake server serves, in a fixed order."""
    return recorded_items(*CORPUS_FILES)


def page_body(items: list[dict[str, Any]], total: int) -> dict[str, Any]:
    """A search page in the recorded envelope."""
    return {"links": [], "items": items, "queryTemplate": {}, "info": {"totalResults": total}}


class FakeSaos:
    """A respx-backed stand-in for `search/judgments` that serves slices of a list of recorded items.

    `calls` holds the query parameters of every request in order; `pages` the zero-based page numbers.
    """

    def __init__(
        self,
        items: list[dict[str, Any]] | None = None,
        *,
        total: int | None = None,
        failing_pages: Iterable[int] = (),
        status: int = 502,
    ) -> None:
        self.items = corpus() if items is None else items
        self.total = len(self.items) if total is None else total
        self.failing_pages = set(failing_pages)
        self.status = status
        self.calls: list[dict[str, str]] = []
        self.urls: list[str] = []
        self.route: respx.Route | None = None

    @property
    def pages(self) -> list[int]:
        return [int(call["pageNumber"]) for call in self.calls]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.calls.append(params)
        self.urls.append(str(request.url))
        size, number = int(params["pageSize"]), int(params["pageNumber"])
        if not 1 <= size <= 100:
            return httpx.Response(400, json={"error": {"httpStatus": "400", "propertyName": "pageSize"}})
        if number in self.failing_pages:
            return httpx.Response(self.status, text="upstream failure")
        window = self.items[number * size : (number + 1) * size]
        return httpx.Response(200, json=page_body(window, self.total))

    def install(self, router: respx.MockRouter) -> FakeSaos:
        """Register the route on `router`."""
        self.route = router.get(SEARCH_URL).mock(side_effect=self._handle)
        return self


def make_saos_client(*, search_ttl: int = 600) -> tuple[SaosClient, EgressHub]:
    """A `SaosClient` on a real egress client against the (mocked) SAOS origin."""
    hub = EgressHub(TTLCache(max_entries=200), EgressDefaults())
    egress: EgressClient = hub.client_for(EgressHost(name=HOST_LABEL, base_url=BASE_URL), IDENTITY)
    return SaosClient(egress, search_ttl=search_ttl), hub


def make_service(
    client: SaosClient,
    store: ResultStore | None = None,
    *,
    today: Callable[[], date] | None = None,
    filter_max_records: int | None = None,
) -> tuple[SaosSearchService, ResultStore]:
    """A search service with a fixed 'today'."""
    result_store = store or ResultStore()
    service = SaosSearchService(
        client, result_store, today=today or (lambda: TODAY), filter_max_records=filter_max_records
    )
    return service, result_store


HarnessFactory = Callable[..., "Harness"]


@dataclass
class Harness:
    """A fake SAOS, the service in front of it and the store the service writes to."""

    fake: FakeSaos
    service: SaosSearchService
    store: ResultStore
    hub: EgressHub
