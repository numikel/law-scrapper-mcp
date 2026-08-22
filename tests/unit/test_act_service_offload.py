"""Size limit and event-loop behaviour of act content loading."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

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
