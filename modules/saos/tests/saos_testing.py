"""Helpers of the SAOS module tests.

No SAOS record is invented here. Pages are cut from the redacted recordings in `tests/fixtures/saos/`
(see `PROVENANCE.md` there), and the fake server only does what the real one was observed to do:
zero-based `pageNumber`, `pageSize` 1..100, `totalResults` on every page, an empty `items` list past the
last page (recorded in `search_out_of_range_page.json`).

A judgment detail is derived, not invented: the recorded detail has the same keys as a search item plus
`source` and `division.type` (`judgment_labour_sentence_metadata.json`, key paths in `PROVENANCE.md`), so a
test detail is a recorded search item with those two keys added and a long text, which is what the real
detail is. The court portal address of every test detail is the recorded one with a suffix.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import respx
from mcp import Client
from mcp.types import Tool

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.egress import CallerIdentity, EgressClient, EgressDefaults, EgressHub
from law_scrapper_mcp.config import settings
from law_scrapper_mcp.services.result_store import ResultStore
from law_scrapper_mcp.source_modules.contract import LoadedModule
from law_scrapper_mcp_saos import module
from law_scrapper_mcp_saos.client import SaosClient
from law_scrapper_mcp_saos.service import SaosSearchService
from law_scrapper_mcp_saos.state import BASE_URL, saos_host

RECORDINGS = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "saos"
SEARCH_URL = f"{BASE_URL}/search/judgments"
DETAIL_URL = re.compile(rf"^{re.escape(BASE_URL)}/judgments/(\d+)$")
DETAIL_FIXTURE = "judgment_labour_sentence_metadata.json"
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


def recorded_portal_url() -> str:
    """The court portal address of the recorded detail."""
    return str(recording(DETAIL_FIXTURE)["data"]["source"]["judgmentUrl"])


def portal_url_for(source_id: int) -> str:
    """A distinct portal address per record, derived from the recorded one."""
    return f"{recorded_portal_url()}&record={source_id}"


def detail_body(
    item: dict[str, Any], *, portal_url: str | None = None, with_division_type: bool = True
) -> dict[str, Any]:
    """A judgment detail derived from a recorded search item (see the module docstring).

    The text is long and the judges are present, as in the real detail, so a test can prove that neither
    reaches an output.
    """
    portal = portal_url if portal_url is not None else portal_url_for(item["id"])
    data: dict[str, Any] = {**item, "source": {"judgmentUrl": portal}, "textContent": item["textContent"] * 40}
    if "division" in item:
        data["division"] = {**item["division"], "type": recording(DETAIL_FIXTURE)["data"]["division"]["type"]}
        if not with_division_type:
            del data["division"]["type"]
    return {"links": [{"rel": "self", "href": item["href"]}], "data": data}


COURT_DICTIONARY = "common_courts_page0.json"
DERIVED_ID_BASE = 9_000_000


def dictionary_division_codes() -> list[str]:
    """Every division code of the recorded common-court dictionary."""
    return [str(division["code"]) for court in recording(COURT_DICTIONARY)["items"] for division in court["divisions"]]


SUPREME_CHAMBERS = "supreme_chambers_metadata_2016.json"


def supreme_chamber_items() -> list[dict[str, Any]]:
    """The 100 recorded Supreme Court records of `SUPREME_CHAMBERS`: id, court type, chambers, division, date."""
    items: list[dict[str, Any]] = recording(SUPREME_CHAMBERS)["items"]
    return items


def derived_list(layout: str) -> list[dict[str, Any]]:
    """A list of search items cut from the recorded ones, one per character of `layout`, with fresh ids.

    `L` is a recorded record of a labour division (code ends in 21), `5` and `8` the same record with a
    recorded division code that ends in 15 and 18, `.` a recorded record of another common-court division,
    `-` a recorded record whose `division` is missing. Only `id` (and the division key where stated) differs
    from the recording, so a test can place hits at exact positions without inventing a SAOS record.
    """
    records = [item for item in corpus() if item["courtType"] == "COMMON" and "division" in item]
    labour = next(item for item in records if item["division"]["code"].endswith("21"))
    other = next(item for item in records if not item["division"]["code"].endswith(("15", "18", "21")))
    codes = dictionary_division_codes()
    code_15 = next(code for code in codes if code.endswith("15"))
    code_18 = next(code for code in codes if code.endswith("18"))
    templates: dict[str, dict[str, Any]] = {
        "L": labour,
        "5": {**labour, "division": {**labour["division"], "code": code_15}},
        "8": {**labour, "division": {**labour["division"], "code": code_18}},
        ".": other,
        "-": {key: value for key, value in labour.items() if key != "division"},
    }
    return [{**templates[char], "id": DERIVED_ID_BASE + index} for index, char in enumerate(layout)]


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
        details: dict[int, dict[str, Any]] | None = None,
        failing_details: Iterable[int] = (),
        missing_details: Iterable[int] = (),
    ) -> None:
        self.items = corpus() if items is None else items
        self.total = len(self.items) if total is None else total
        self.failing_pages = set(failing_pages)
        self.status = status
        self._detail_overrides = details or {}
        self.failing_details = set(failing_details)
        self.missing_details = set(missing_details)
        self.calls: list[dict[str, str]] = []
        self.urls: list[str] = []
        self.detail_urls: list[str] = []
        self.route: respx.Route | None = None
        self.detail_route: respx.Route | None = None

    @property
    def pages(self) -> list[int]:
        return [int(call["pageNumber"]) for call in self.calls]

    @property
    def detail_ids(self) -> list[int]:
        """Identifiers asked for, one entry per request (a retried request counts again)."""
        return [int(url.rsplit("/", 1)[-1]) for url in self.detail_urls]

    @property
    def requests(self) -> int:
        """Every request that reached the fake SAOS, search and detail."""
        return len(self.calls) + len(self.detail_urls)

    def _handle_detail(self, request: httpx.Request) -> httpx.Response:
        self.detail_urls.append(str(request.url))
        source_id = int(request.url.path.rsplit("/", 1)[-1])
        if source_id in self.failing_details:
            return httpx.Response(self.status, text="upstream failure")
        if source_id in self.missing_details:
            return httpx.Response(404)
        if source_id in self._detail_overrides:
            return httpx.Response(200, json=self._detail_overrides[source_id])
        # Built on demand: a list of thousands of items would otherwise cost a file read per item up front.
        item = next((candidate for candidate in self.items if candidate["id"] == source_id), None)
        if item is None:
            return httpx.Response(404)
        return httpx.Response(200, json=detail_body(item))

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
        self.detail_route = router.get(url__regex=DETAIL_URL).mock(side_effect=self._handle_detail)
        return self


class FakeClock:
    """A clock that only moves when the limiter waits (see `conftest.instant_waits`), so pacing costs no time."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


CLOCK = FakeClock()


def make_saos_client(*, search_ttl: int = 600) -> tuple[SaosClient, EgressHub]:
    """A `SaosClient` on a real egress client, registered with the module's own host and pace."""
    hub = EgressHub(TTLCache(max_entries=200), EgressDefaults(), clock=CLOCK)
    egress: EgressClient = hub.client_for(saos_host(settings), IDENTITY)
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
    client: SaosClient


def loaded() -> LoadedModule:
    """The SAOS module as the core would hand it to `build_app`."""
    return LoadedModule(module=module, distribution="law-scrapper-mcp-saos", version="0.1.0")


@asynccontextmanager
async def connected(router: respx.MockRouter, fake: FakeSaos | None = None) -> AsyncIterator[Client]:
    """A client on an in-memory server with the SAOS module and a fake SAOS behind it.

    A context manager rather than a fixture: the client's task group must be entered and left by one task.
    """
    # Imported here: importing the server runs discovery, so a broken descriptor fails a named test.
    from law_scrapper_mcp.server import build_app

    (fake or FakeSaos()).install(router)
    async with Client(build_app([loaded()])) as client:
        yield client


@dataclass
class Session:
    """An in-memory client with the tools it listed, for calls that must succeed or must refuse."""

    client: Client
    tools: dict[str, Tool]

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool, require success, validate the payload against the tool's `outputSchema`, return `data`."""
        result = await self.client.call_tool(name, arguments)
        assert result.is_error is False, result.content
        payload = result.structured_content
        assert payload is not None
        schema = self.tools[name].output_schema
        assert schema is not None, name
        jsonschema.validate(payload, schema)
        assert isinstance(payload["hints"], list)
        data: dict[str, Any] = payload["data"]
        return data

    async def fail(self, name: str, arguments: dict[str, Any]) -> str:
        """Call a tool that must refuse; return the text the agent reads."""
        result = await self.client.call_tool(name, arguments)
        assert result.is_error is True, result.structured_content
        assert result.structured_content is None
        return str(result.content[0].text)  # type: ignore[union-attr]


@asynccontextmanager
async def saos_session(router: respx.MockRouter, fake: FakeSaos | None = None) -> AsyncIterator[Session]:
    """The server with only the SAOS module, a fake SAOS behind it, and a session on top."""
    async with connected(router, fake) as client:
        yield Session(client=client, tools={tool.name: tool for tool in (await client.list_tools()).tools})
