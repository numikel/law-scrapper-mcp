"""Routing of the services through the act-source registry.

Two promises are pinned here. With an empty registry (or one that serves other codes) every service
issues exactly the upstream calls it always did. With a code registered, the service hands the
request to the source and never contacts the Sejm client.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response

from fake_act_source import FAKE_PUBLISHER, FakeActSource
from law_scrapper_mcp.client.exceptions import ContentTooLargeError
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.models.enums import DetailLevel
from law_scrapper_mcp.services.act_service import ActService
from law_scrapper_mcp.services.act_sources import ActSourceRegistry, SourceSearchQuery
from law_scrapper_mcp.services.changes_service import ChangesService
from law_scrapper_mcp.services.content_processor import ContentProcessor
from law_scrapper_mcp.services.document_store import DocumentStore
from law_scrapper_mcp.services.relationship_service import RelationshipService
from law_scrapper_mcp.services.result_store import ResultStore
from law_scrapper_mcp.services.search_service import SearchService

pytestmark = pytest.mark.asyncio

SEJM = "https://api.sejm.gov.pl/eli"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "edziennik"
ELI = f"{FAKE_PUBLISHER}/2025/7"


def _registry(source: FakeActSource) -> ActSourceRegistry:
    registry = ActSourceRegistry()
    registry.register(source, publishers=[FAKE_PUBLISHER])
    return registry


def _sejm_calls(since: int = 0) -> list[tuple[str, dict[str, str]]]:
    """Every request the mocked Sejm saw, as (path, query parameters)."""
    return [(call.request.url.path, dict(call.request.url.params)) for call in list(respx.calls)[since:]]


def _mock_sejm(act_detail: dict[str, Any], search_results: dict[str, Any]) -> None:
    respx.get(f"{SEJM}/acts/search").mock(return_value=Response(200, json=search_results))
    respx.get(f"{SEJM}/acts/DU/2024/1").mock(return_value=Response(200, json=act_detail))
    respx.get(f"{SEJM}/acts/DU/2024/1/struct").mock(return_value=Response(404))
    respx.get(f"{SEJM}/acts/DU/2024/1/references").mock(return_value=Response(200, json={}))


# --- Parity: the Sejm path is the one it always was -----------------------------------------


EXPECTED_SEJM_CALLS: dict[str, list[tuple[str, dict[str, str]]]] = {
    "search": [("/eli/acts/search", {"publisher": "DU", "year": "2024", "keyword": "a,b", "title": "x", "limit": "5"})],
    "browse": [("/eli/acts/search", {"publisher": "DU", "year": "2024", "limit": "10", "offset": "5"})],
    "track": [
        (
            "/eli/acts/search",
            {"publisher": "DU", "dateFrom": "2024-01-01", "dateTo": "2024-12-31", "keyword": "a", "limit": "20"},
        )
    ],
    "details": [("/eli/acts/DU/2024/1", {}), ("/eli/acts/DU/2024/1/struct", {})],
    "relations": [("/eli/acts/DU/2024/1/references", {})],
}


async def _run_all(
    client: SejmApiClient, registry: ActSourceRegistry | None, store: DocumentStore
) -> dict[str, list[tuple[str, dict[str, str]]]]:
    """Run each routed operation against DU and record the upstream calls per operation."""
    kwargs: dict[str, Any] = {} if registry is None else {"sources": registry}
    search = SearchService(client, ResultStore(), **kwargs)
    changes = ChangesService(client, ResultStore(), **kwargs)
    acts = ActService(client, store, ContentProcessor(), **kwargs)
    relations = RelationshipService(client, **kwargs)

    seen: dict[str, list[tuple[str, dict[str, str]]]] = {}
    operations = {
        "search": lambda: search.search("DU", year=2024, keywords=["a", "b"], title="x", limit=5),
        "browse": lambda: search.browse("DU", 2024, limit=10, offset=5),
        "track": lambda: changes.track_changes("DU", "2024-01-01", "2024-12-31", ["a"]),
        "details": lambda: acts.get_details("DU/2024/1"),
        "relations": lambda: relations.get_relationships("DU/2024/1"),
    }
    for name, operation in operations.items():
        before = len(respx.calls)
        await operation()
        seen[name] = _sejm_calls(before)
    return seen


@respx.mock
@pytest.mark.parametrize("variant", ["no_registry", "empty_registry", "registry_for_other_codes"])
async def test_empty_registry_issues_the_same_upstream_calls(
    variant: str,
    mock_client: SejmApiClient,
    document_store: DocumentStore,
    act_detail: dict[str, Any],
    search_results: dict[str, Any],
) -> None:
    """The Sejm path of every routed service is byte-for-byte the 4.4.2 one when no source serves DU."""
    _mock_sejm(act_detail, search_results)
    registry: ActSourceRegistry | None
    if variant == "no_registry":
        registry = None
    elif variant == "empty_registry":
        registry = ActSourceRegistry()
    else:
        registry = _registry(FakeActSource())

    seen = await _run_all(mock_client, registry, document_store)

    assert seen == EXPECTED_SEJM_CALLS


@respx.mock
async def test_a_source_serving_a_code_leaves_the_pdf_address_of_the_sejm_alone(
    mock_client: SejmApiClient, document_store: DocumentStore
) -> None:
    service = ActService(mock_client, document_store, ContentProcessor(), sources=_registry(FakeActSource()))

    assert service.pdf_url("DU", 2024, 1) == f"{SEJM}/acts/DU/2024/1/text.pdf"


@respx.mock
async def test_pdf_url_comes_from_the_source_for_its_code(
    mock_client: SejmApiClient, document_store: DocumentStore
) -> None:
    service = ActService(mock_client, document_store, ContentProcessor(), sources=_registry(FakeActSource()))

    assert (
        service.pdf_url(FAKE_PUBLISHER, 2025, 7)
        == f"https://fake.example/api/eli/acts/{FAKE_PUBLISHER}/2025/7/text.pdf"
    )


# --- Routing: a registered code never reaches the Sejm --------------------------------------


@pytest.fixture
def source() -> FakeActSource:
    return FakeActSource(total=30)


@pytest.fixture
def sejm_must_stay_silent() -> Iterator[respx.Route]:
    """A catch-all route on the Sejm host; its call count is the assertion.

    The fixture opens the mock itself and discards the route on exit, so it cannot leak into
    other tests through the global router.
    """
    with respx.mock(assert_all_called=False) as router:
        yield router.route(host="api.sejm.gov.pl").mock(return_value=Response(500))


async def test_registered_publisher_search_never_touches_the_sejm(
    mock_client: SejmApiClient, source: FakeActSource, sejm_must_stay_silent: respx.Route
) -> None:
    service = SearchService(mock_client, ResultStore(), sources=_registry(source))

    output = await service.search(
        FAKE_PUBLISHER,
        year=2025,
        keywords=["budżet", "oświata"],
        title="x",
        act_type="Uchwała",
        pub_date_from="2025-01-01",
    )

    assert sejm_must_stay_silent.call_count == 0
    ((_, publisher, query),) = source.calls
    assert publisher == FAKE_PUBLISHER
    assert query == SourceSearchQuery(
        year=2025,
        keywords=("budżet", "oświata"),
        title="x",
        act_type="Uchwała",
        pub_date_from="2025-01-01",
        limit=20,
        offset=0,
    )
    assert output.returned_count == 20
    assert output.total_count == 30
    assert output.result_set_id is not None
    assert (
        output.query_summary
        == f"publisher={FAKE_PUBLISHER} | year=2025 | keywords=budżet,oświata | title=x | type=Uchwała"
    )


async def test_search_pages_with_offset_and_stores_the_window(
    mock_client: SejmApiClient, source: FakeActSource, sejm_must_stay_silent: respx.Route
) -> None:
    service = SearchService(mock_client, ResultStore(), sources=_registry(source))

    output = await service.search(FAKE_PUBLISHER, limit=10, offset=25)

    assert [item.pos for item in output.results] == [26, 27, 28, 29, 30]
    assert output.page_info.offset == 25
    assert output.total_count == 30
    assert sejm_must_stay_silent.call_count == 0


@respx.mock
async def test_search_limit_above_the_maximum_is_rejected_not_cut(
    mock_client: SejmApiClient, source: FakeActSource
) -> None:
    service = SearchService(mock_client, ResultStore(), sources=_registry(source))

    with pytest.raises(ValueError, match=r"nie może przekraczać 100") as excinfo:
        await service.search(FAKE_PUBLISHER, limit=101)

    assert source.name in str(excinfo.value)
    assert source.calls == [], "rejected before the source is asked"


@respx.mock
async def test_search_limit_of_exactly_the_maximum_is_accepted(
    mock_client: SejmApiClient, source: FakeActSource
) -> None:
    service = SearchService(mock_client, ResultStore(), sources=_registry(FakeActSource(total=500)))

    output = await service.search(FAKE_PUBLISHER, limit=100)

    assert output.returned_count == 100


@respx.mock
async def test_search_limit_above_the_maximum_is_still_unclamped_for_the_sejm(
    mock_client: SejmApiClient, act_detail: dict[str, Any], search_results: dict[str, Any]
) -> None:
    """The ceiling is a source rule; the documented Sejm behaviour (no upper clamp) is untouched."""
    _mock_sejm(act_detail, search_results)
    service = SearchService(mock_client, ResultStore(), sources=_registry(FakeActSource()))

    await service.search("DU", limit=250)

    assert _sejm_calls()[0][1]["limit"] == "250"


@respx.mock
async def test_minimal_detail_level_clears_what_a_source_filled_in(
    mock_client: SejmApiClient, source: FakeActSource
) -> None:
    service = SearchService(mock_client, ResultStore(), sources=_registry(source))

    minimal = await service.search(FAKE_PUBLISHER, limit=1, detail_level=DetailLevel.MINIMAL)
    standard = await service.search(FAKE_PUBLISHER, limit=1)

    item = minimal.results[0]
    assert (item.type, item.promulgation_date, item.effective_date, item.in_force) == (None, None, None, None)
    assert standard.results[0].type == "Uchwała"
    assert standard.results[0].promulgation_date == "2025-02-03"


async def test_browse_goes_to_the_source_with_the_year_only(
    mock_client: SejmApiClient, source: FakeActSource, sejm_must_stay_silent: respx.Route
) -> None:
    service = SearchService(mock_client, ResultStore(), sources=_registry(source))

    output = await service.browse(FAKE_PUBLISHER, 2025, limit=10, offset=10)

    ((_, _, query),) = source.calls
    assert query == SourceSearchQuery(year=2025, limit=10, offset=10)
    assert output.query_summary == f"publisher={FAKE_PUBLISHER} | year=2025"
    assert output.returned_count == 10
    assert sejm_must_stay_silent.call_count == 0


async def test_track_changes_asks_for_the_publication_window(
    mock_client: SejmApiClient, source: FakeActSource, sejm_must_stay_silent: respx.Route
) -> None:
    service = ChangesService(mock_client, ResultStore(), sources=_registry(source))

    output = await service.track_changes(FAKE_PUBLISHER, "2025-01-01", "2025-06-30", ["budżet"], limit=5, offset=5)

    ((_, publisher, query),) = source.calls
    assert publisher == FAKE_PUBLISHER
    assert query == SourceSearchQuery(
        keywords=("budżet",), pub_date_from="2025-01-01", pub_date_to="2025-06-30", limit=5, offset=5
    )
    assert query.date_from is None and query.date_to is None, "the window is publication dates, not effective dates"
    assert output.date_range == "2025-01-01 to 2025-06-30"
    assert output.publisher == FAKE_PUBLISHER
    assert output.keywords == ["budżet"]
    assert len(output.changes) == 5
    assert output.total_count == 30
    assert output.result_set_id is not None
    assert sejm_must_stay_silent.call_count == 0


@respx.mock
async def test_track_changes_ends_the_window_today_by_default(
    mock_client: SejmApiClient, source: FakeActSource
) -> None:
    service = ChangesService(mock_client, ResultStore(), sources=_registry(source))

    output = await service.track_changes(FAKE_PUBLISHER, "2025-01-01")

    ((_, _, query),) = source.calls
    today = datetime.now().strftime("%Y-%m-%d")
    assert query.pub_date_to == today
    assert output.date_range == f"2025-01-01 to {today}"


async def test_relationships_come_from_the_source_and_keep_the_exact_key_filter(
    mock_client: SejmApiClient, sejm_must_stay_silent: respx.Route
) -> None:
    source = FakeActSource(relations={"Akty zmieniane": [{"id": 1}, {"id": 2}], "Podstawa prawna": [{"id": 3}]})
    service = RelationshipService(mock_client, sources=_registry(source))

    everything = await service.get_relationships(ELI)
    filtered = await service.get_relationships(ELI, "Podstawa prawna")

    assert everything.total_count == 3
    assert filtered.relationships == {"Podstawa prawna": [{"id": 3}]}
    assert filtered.total_count == 1
    assert source.calls[0] == ("get_relationships", FAKE_PUBLISHER, 2025, 7)
    assert sejm_must_stay_silent.call_count == 0


async def test_details_skip_struct_and_do_not_load_unless_asked(
    mock_client: SejmApiClient, document_store: DocumentStore, source: FakeActSource, sejm_must_stay_silent: respx.Route
) -> None:
    service = ActService(mock_client, document_store, ContentProcessor(), sources=_registry(source))

    details = await service.get_details(ELI)

    assert details.content_status == "not_requested"
    assert details.is_loaded is False
    assert details.toc == []
    assert source.calls == [("get_details", FAKE_PUBLISHER, 2025, 7)], "no /struct, no PDF"
    assert sejm_must_stay_silent.call_count == 0


# --- Content: PDF only, with three outcomes ----------------------------------------------------


def _service(client: SejmApiClient, store: DocumentStore, source: FakeActSource) -> ActService:
    return ActService(client, store, ContentProcessor(), sources=_registry(source))


async def test_a_pdf_with_a_text_layer_loads(
    mock_client: SejmApiClient, document_store: DocumentStore, sejm_must_stay_silent: respx.Route
) -> None:
    source = FakeActSource(pdf=(FIXTURES / "text_layer.pdf").read_bytes())

    details = await _service(mock_client, document_store, source).get_details(ELI, load_content=True)

    assert details.content_status == "loaded"
    assert details.is_loaded is True
    assert await document_store.is_loaded(ELI)
    ((_, _, _, _, max_bytes),) = [call for call in source.calls if call[0] == "get_pdf"]
    assert max_bytes > 0, "the size budget reaches the download"
    assert sejm_must_stay_silent.call_count == 0


@respx.mock
async def test_a_scanned_pdf_is_unavailable_and_remembered(
    mock_client: SejmApiClient, document_store: DocumentStore
) -> None:
    source = FakeActSource(pdf=(FIXTURES / "scan_no_text.pdf").read_bytes())
    service = _service(mock_client, document_store, source)

    first = await service.get_details(ELI, load_content=True)
    second = await service.get_details(ELI, load_content=True)

    assert first.content_status == "unavailable"
    assert first.is_loaded is False
    assert second.content_status == "unavailable"
    assert [call[0] for call in source.calls].count("get_pdf") == 1, "the verdict is cached, the PDF fetched once"
    assert not await document_store.is_loaded(ELI)


@respx.mock
async def test_unreadable_bytes_are_unavailable_with_a_warning(
    mock_client: SejmApiClient, document_store: DocumentStore, caplog: pytest.LogCaptureFixture
) -> None:
    source = FakeActSource(pdf=b"this is not a pdf")

    with caplog.at_level(logging.WARNING, logger="law_scrapper_mcp.services.act_service"):
        details = await _service(mock_client, document_store, source).get_details(ELI, load_content=True)

    assert details.content_status == "unavailable"
    assert any("could not be read" in record.getMessage() for record in caplog.records)


@respx.mock
async def test_a_missing_pdf_is_unavailable(mock_client: SejmApiClient, document_store: DocumentStore) -> None:
    """A 404 on the file is the source saying there is no text; the metadata is still returned."""
    source = FakeActSource(pdf_missing=True)

    details = await _service(mock_client, document_store, source).get_details(ELI, load_content=True)

    assert details.title == "Uchwała w sprawie budżetu"
    assert details.content_status == "unavailable"


@respx.mock
async def test_metadata_without_a_pdf_never_asks_for_one(
    mock_client: SejmApiClient, document_store: DocumentStore
) -> None:
    source = FakeActSource(has_pdf=False)

    details = await _service(mock_client, document_store, source).get_details(ELI, load_content=True)

    assert details.content_status == "unavailable"
    assert [call[0] for call in source.calls] == ["get_details"]


class _FakePage:
    def __init__(self, text: str) -> None:
        self.chars = list(text)
        self.images: list[object] = []
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _FakePdf:
    def __init__(self, texts: list[str]) -> None:
        self.pages = [_FakePage(text) for text in texts]

    def __enter__(self) -> _FakePdf:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


@respx.mock
async def test_a_garbled_text_layer_loads_with_a_warning(
    mock_client: SejmApiClient,
    document_store: DocumentStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Synthetic: the `(cid:N)` shape is a hypothesis about old journal PDFs, so a stand-in reader is used."""
    garbled = "Art. 1. " + "(cid:12)(cid:7)" * 40 + " koniec.\nArt. 2. Uchwała wchodzi w życie."
    monkeypatch.setattr("pdfplumber.open", lambda _stream: _FakePdf([garbled]))
    source = FakeActSource(pdf=b"%PDF-stand-in")

    with caplog.at_level(logging.WARNING, logger="law_scrapper_mcp.services.act_service"):
        details = await _service(mock_client, document_store, source).get_details(ELI, load_content=True)

    assert details.content_status == "loaded"
    assert await document_store.is_loaded(ELI)
    warning = next(record.getMessage() for record in caplog.records if "garbled" in record.getMessage())
    assert ELI in warning
    assert "80" in warning, "the marker count is in the message"


@respx.mock
async def test_an_oversized_source_pdf_is_refused_with_its_address(
    mock_client: SejmApiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DocumentStore(max_documents=10, max_size_bytes=64, ttl=7200)
    monkeypatch.setattr("law_scrapper_mcp.services.act_service.settings.doc_store_max_size_bytes", 64)
    source = FakeActSource(pdf=b"x" * 200)

    with pytest.raises(ContentTooLargeError, match="fake.example"):
        await _service(mock_client, store, source).get_details(ELI, load_content=True)
