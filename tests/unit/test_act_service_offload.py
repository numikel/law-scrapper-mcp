"""Size limit and event-loop behaviour of act content loading."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import pytest
import respx
from httpx import AsyncByteStream, Response

from law_scrapper_mcp.client.exceptions import ContentTooLargeError
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.services.act_service import ActService
from law_scrapper_mcp.services.content_processor import ContentProcessor
from law_scrapper_mcp.services.document_store import DocumentStore

pytestmark = pytest.mark.asyncio

PDF_URL = "https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.pdf"


class CountingProcessor(ContentProcessor):
    """Records how often each conversion entry point was entered."""

    def __init__(self) -> None:
        self.html_calls = 0
        self.pdf_calls = 0
        self.index_calls = 0

    def html_to_markdown(self, html: str) -> str:
        self.html_calls += 1
        return "# Art. 1. Treść testowa"

    def pdf_to_text(self, pdf_bytes: bytes) -> str:
        self.pdf_calls += 1
        return "Art. 1. Treść testowa"

    def index_sections(self, markdown: str):
        self.index_calls += 1
        return super().index_sections(markdown)


@pytest.fixture
def counting_processor() -> CountingProcessor:
    return CountingProcessor()


@pytest.fixture
def service(
    mock_client: SejmApiClient,
    document_store: DocumentStore,
    counting_processor: CountingProcessor,
) -> ActService:
    return ActService(
        client=mock_client,
        document_store=document_store,
        content_processor=counting_processor,
    )


def _mock_act_endpoints(html: str, act_detail: dict) -> None:
    respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1").mock(return_value=Response(200, json=act_detail))
    respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/struct").mock(return_value=Response(404))
    respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.html").mock(return_value=Response(200, text=html))


@respx.mock
async def test_oversized_html_is_refused_before_conversion(
    service: ActService,
    counting_processor: CountingProcessor,
    act_detail: dict,
    monkeypatch,
) -> None:
    """The converter must never see content it is not allowed to process."""
    from law_scrapper_mcp.services import act_service as act_service_module

    monkeypatch.setattr(act_service_module.settings, "doc_store_max_size_bytes", 1024)
    _mock_act_endpoints("<html><body>" + "x" * 4096 + "</body></html>", act_detail)

    with pytest.raises(ContentTooLargeError) as excinfo:
        await service.get_details("DU/2024/1", load_content=True)

    assert counting_processor.html_calls == 0
    assert counting_processor.index_calls == 0
    message = str(excinfo.value)
    assert "przekracza limit" in message
    assert PDF_URL in message


@respx.mock
async def test_content_within_the_limit_still_loads(
    service: ActService,
    counting_processor: CountingProcessor,
    document_store: DocumentStore,
    act_detail: dict,
) -> None:
    """The happy path must be untouched by the guard."""
    _mock_act_endpoints("<html><body><h1>Ustawa</h1><p>Art. 1. Treść.</p></body></html>", act_detail)

    result = await service.get_details("DU/2024/1", load_content=True)

    assert result.is_loaded is True
    assert counting_processor.html_calls == 1
    assert counting_processor.index_calls == 1
    assert await document_store.is_loaded("DU/2024/1") is True


@respx.mock
async def test_oversized_pdf_is_refused_before_conversion(
    service: ActService,
    counting_processor: CountingProcessor,
    act_detail: dict,
    monkeypatch,
) -> None:
    """The PDF branch's try/except/else must not swallow a size refusal into the fetch-failure fallback."""
    from law_scrapper_mcp.services import act_service as act_service_module

    monkeypatch.setattr(act_service_module.settings, "doc_store_max_size_bytes", 1024)
    act_detail_no_html = act_detail.copy()
    act_detail_no_html["textHTML"] = None

    respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1").mock(return_value=Response(200, json=act_detail_no_html))
    respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/struct").mock(return_value=Response(404))
    respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.pdf").mock(
        return_value=Response(200, content=b"x" * 4096)
    )

    with pytest.raises(ContentTooLargeError) as excinfo:
        await service.get_details("DU/2024/1", load_content=True)

    assert counting_processor.pdf_calls == 0
    message = str(excinfo.value)
    assert "przekracza limit" in message
    assert PDF_URL in message


class RecordingStream(AsyncByteStream):
    """A response body served in chunks that counts how many were pulled."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.pulled = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.pulled += 1
            yield chunk


@respx.mock
async def test_oversized_html_is_refused_while_it_streams(
    service: ActService,
    counting_processor: CountingProcessor,
    act_detail: dict,
    monkeypatch,
) -> None:
    """The budget reaches the download itself: the body is never materialised past the limit (#19).

    The refusal still names the act and the source file, as the post-hoc gate's does,
    because that is the sentence an agent can act on.
    """
    from law_scrapper_mcp.services import act_service as act_service_module

    monkeypatch.setattr(act_service_module.settings, "doc_store_max_size_bytes", 1024)
    stream = RecordingStream([b"<p>" + b"x" * 509] * 4)
    respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1").mock(return_value=Response(200, json=act_detail))
    respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/struct").mock(return_value=Response(404))
    respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.html").mock(
        return_value=Response(200, stream=stream, headers={"Content-Type": "text/html; charset=utf-8"})
    )

    with pytest.raises(ContentTooLargeError) as excinfo:
        await service.get_details("DU/2024/1", load_content=True)

    assert stream.pulled == 3
    assert counting_processor.html_calls == 0
    message = str(excinfo.value)
    assert "DU/2024/1" in message
    assert "przekracza limit" in message
    assert PDF_URL in message


@respx.mock
async def test_oversized_pdf_is_refused_while_it_streams(
    service: ActService,
    counting_processor: CountingProcessor,
    act_detail: dict,
    monkeypatch,
) -> None:
    """The PDF branch's fetch-failure fallback must not swallow a refusal raised mid-download."""
    from law_scrapper_mcp.services import act_service as act_service_module

    monkeypatch.setattr(act_service_module.settings, "doc_store_max_size_bytes", 1024)
    act_detail_no_html = act_detail.copy()
    act_detail_no_html["textHTML"] = None
    stream = RecordingStream([b"x" * 512] * 4)
    respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1").mock(return_value=Response(200, json=act_detail_no_html))
    respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/struct").mock(return_value=Response(404))
    respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.pdf").mock(return_value=Response(200, stream=stream))

    with pytest.raises(ContentTooLargeError) as excinfo:
        await service.get_details("DU/2024/1", load_content=True)

    assert stream.pulled == 3
    assert counting_processor.pdf_calls == 0
    assert PDF_URL in str(excinfo.value)


class SlowProcessor(ContentProcessor):
    """Burns wall-clock time synchronously, the way markdownify does."""

    def html_to_markdown(self, html: str) -> str:
        time.sleep(0.3)
        return "# Art. 1. Treść testowa"


@respx.mock
async def test_conversion_leaves_the_event_loop_free(
    mock_client: SejmApiClient,
    document_store: DocumentStore,
    act_detail: dict,
) -> None:
    """Behavioural proof, not an implementation check.

    A concurrent coroutine must keep making progress while a document is being
    converted. Before the offload it could not: the conversion held the loop
    for its whole duration, `/health` included.
    """
    service = ActService(
        client=mock_client,
        document_store=document_store,
        content_processor=SlowProcessor(),
    )
    _mock_act_endpoints("<html><body><h1>Ustawa</h1></body></html>", act_detail)

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    background = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    try:
        await service.get_details("DU/2024/1", load_content=True)
    finally:
        background.cancel()

    # ~30 ticks fit into a 0.3 s conversion; without the offload it is 1.
    assert ticks > 5


class ExpandingPdfProcessor(CountingProcessor):
    """A small PDF payload that extracts into a large body of text.

    PDF text streams are Flate-compressed, so a payload comfortably under the
    limit can expand past it once extracted. Guarding the conversion input
    alone lets such a document reach `DocumentStore.load`, which truncates it
    with nothing but a log line - the silent loss D6 rejected.
    """

    def pdf_to_text(self, pdf_bytes: bytes) -> str:
        self.pdf_calls += 1
        return "Art. 1. " + "x" * 4096


@respx.mock
async def test_pdf_expanding_past_the_limit_is_refused_not_truncated(
    mock_client: SejmApiClient,
    document_store: DocumentStore,
    act_detail: dict,
    monkeypatch,
) -> None:
    """The limit governs the whole document path, not just its input (D7)."""
    from law_scrapper_mcp.services import act_service as act_service_module

    monkeypatch.setattr(act_service_module.settings, "doc_store_max_size_bytes", 1024)
    processor = ExpandingPdfProcessor()
    service = ActService(
        client=mock_client,
        document_store=document_store,
        content_processor=processor,
    )

    act_detail_no_html = act_detail.copy()
    act_detail_no_html["textHTML"] = None
    respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1").mock(return_value=Response(200, json=act_detail_no_html))
    respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/struct").mock(return_value=Response(404))
    respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.pdf").mock(
        return_value=Response(200, content=b"x" * 512)
    )

    with pytest.raises(ContentTooLargeError) as excinfo:
        await service.get_details("DU/2024/1", load_content=True)

    assert processor.pdf_calls == 1
    assert processor.index_calls == 0
    assert await document_store.is_loaded("DU/2024/1") is False
    message = str(excinfo.value)
    assert "przekracza limit" in message
    assert PDF_URL in message
