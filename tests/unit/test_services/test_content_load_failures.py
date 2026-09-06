"""The three disjoint outcomes of loading act content (cluster 10, D2/D5)."""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import Response

from law_scrapper_mcp.client.exceptions import ApiUnavailableError
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.services.act_service import ActService
from law_scrapper_mcp.services.content_processor import ContentProcessor
from law_scrapper_mcp.services.document_store import DocumentStore

ACT_URL = "https://api.sejm.gov.pl/eli/acts/DU/2024/1"


@pytest.fixture
def service(
    mock_client: SejmApiClient,
    document_store: DocumentStore,
    content_processor: ContentProcessor,
) -> ActService:
    return ActService(
        client=mock_client,
        document_store=document_store,
        content_processor=content_processor,
    )


def _mock_metadata(act_detail: dict, *, html: bool, pdf: bool) -> None:
    """Route the two metadata calls every `get_details` makes."""
    detail = act_detail.copy()
    detail["textHTML"] = "/eli/acts/DU/2024/1/text.html" if html else None
    detail["textPDF"] = "/eli/acts/DU/2024/1/text.pdf" if pdf else None
    respx.get(ACT_URL).mock(return_value=Response(200, json=detail))
    respx.get(f"{ACT_URL}/struct").mock(return_value=Response(404))


class TestTransientFailuresPropagate:
    """A failing upstream must not be reported as an act without text (A1-A3)."""

    pytestmark = pytest.mark.asyncio

    @respx.mock
    async def test_open_breaker_propagates(
        self, service: ActService, act_detail: dict, document_store: DocumentStore
    ) -> None:
        """A non-httpx exception (what an open breaker raises) survives the retry loop and propagates (A1)."""
        _mock_metadata(act_detail, html=True, pdf=True)
        respx.get(f"{ACT_URL}/text.html").mock(
            side_effect=ApiUnavailableError("Circuit breaker otwarty", status_code=503)
        )

        with pytest.raises(ApiUnavailableError):
            await service.get_details("DU/2024/1", load_content=True)

        assert not await document_store.is_loaded("DU/2024/1")

    @respx.mock
    async def test_timeout_propagates(self, service: ActService, act_detail: dict) -> None:
        """A read timeout is transient, never an absence of content (A2)."""
        _mock_metadata(act_detail, html=True, pdf=True)
        respx.get(f"{ACT_URL}/text.html").mock(side_effect=httpx.TimeoutException("read timed out"))

        with pytest.raises(ApiUnavailableError):
            await service.get_details("DU/2024/1", load_content=True)

    @respx.mock
    async def test_html_5xx_propagates(self, service: ActService, act_detail: dict) -> None:
        """HTTP 500 on text.html surfaces as unavailability (A2)."""
        _mock_metadata(act_detail, html=True, pdf=True)
        respx.get(f"{ACT_URL}/text.html").mock(return_value=Response(500, text="boom"))

        with pytest.raises(ApiUnavailableError):
            await service.get_details("DU/2024/1", load_content=True)

    @respx.mock
    async def test_pdf_5xx_propagates(self, service: ActService, act_detail: dict) -> None:
        """HTTP 503 on text.pdf is transient, unlike a 404 (A2, D5)."""
        _mock_metadata(act_detail, html=False, pdf=True)
        respx.get(f"{ACT_URL}/text.pdf").mock(return_value=Response(503, text="maintenance"))

        with pytest.raises(ApiUnavailableError):
            await service.get_details("DU/2024/1", load_content=True)

    @respx.mock
    async def test_converter_failure_propagates(
        self, mock_client: SejmApiClient, document_store: DocumentStore, act_detail: dict
    ) -> None:
        """An exception outside the D5 table takes the default branch: propagate (A3).

        An unknown failure must never be allowed to look like missing content —
        that is the whole point of replacing the blacklist with a whitelist.
        """

        class ExplodingProcessor(ContentProcessor):
            def html_to_markdown(self, html: str) -> str:
                raise RuntimeError("markdownify blew up")

        service = ActService(
            client=mock_client,
            document_store=document_store,
            content_processor=ExplodingProcessor(),
        )
        _mock_metadata(act_detail, html=True, pdf=True)
        respx.get(f"{ACT_URL}/text.html").mock(return_value=Response(200, text="<html><body>x</body></html>"))

        with pytest.raises(RuntimeError, match="markdownify blew up"):
            await service.get_details("DU/2024/1", load_content=True)

        assert not await document_store.is_loaded("DU/2024/1")


class TestPermanentAbsenceIsASuccess:
    """An act that has no readable text is a fact, not a failure (A5-A7)."""

    pytestmark = pytest.mark.asyncio

    @respx.mock
    async def test_missing_pdf_is_a_documented_absence(
        self, service: ActService, act_detail: dict, document_store: DocumentStore
    ) -> None:
        """404 on the only available format: success, stated plainly (A5)."""
        _mock_metadata(act_detail, html=False, pdf=True)
        respx.get(f"{ACT_URL}/text.pdf").mock(return_value=Response(404))

        result = await service.get_details("DU/2024/1", load_content=True)

        assert result.content_status == "unavailable"
        assert result.is_loaded is False
        assert result.title  # metadata still came through
        assert not await document_store.is_loaded("DU/2024/1")

    @respx.mock
    async def test_empty_extraction_is_a_documented_absence(
        self, mock_client: SejmApiClient, document_store: DocumentStore, act_detail: dict
    ) -> None:
        """An empty extraction stores nothing at all — no stand-in sentence (A6)."""

        class EmptyProcessor(ContentProcessor):
            def pdf_to_text(self, pdf_bytes: bytes) -> str:
                return "   \n  "

        service = ActService(
            client=mock_client,
            document_store=document_store,
            content_processor=EmptyProcessor(),
        )
        _mock_metadata(act_detail, html=False, pdf=True)
        respx.get(f"{ACT_URL}/text.pdf").mock(return_value=Response(200, content=b"%PDF-1.4 fake"))

        result = await service.get_details("DU/2024/1", load_content=True)

        assert result.content_status == "unavailable"
        assert not await document_store.is_loaded("DU/2024/1")

    @respx.mock
    async def test_empty_html_extraction_is_a_documented_absence(
        self, mock_client: SejmApiClient, document_store: DocumentStore, act_detail: dict
    ) -> None:
        """The symmetric HTML case: an empty extraction is an absence, not a load (I1)."""

        class EmptyProcessor(ContentProcessor):
            def html_to_markdown(self, html: str) -> str:
                return "   \n  "

        service = ActService(
            client=mock_client,
            document_store=document_store,
            content_processor=EmptyProcessor(),
        )
        _mock_metadata(act_detail, html=True, pdf=False)
        respx.get(f"{ACT_URL}/text.html").mock(return_value=Response(200, text="<html><body></body></html>"))

        result = await service.get_details("DU/2024/1", load_content=True)

        assert result.content_status == "unavailable"
        assert not await document_store.is_loaded("DU/2024/1")

    @respx.mock
    async def test_neither_format_present_sends_no_request(
        self, service: ActService, act_detail: dict, document_store: DocumentStore
    ) -> None:
        """Metadata already proves the fetch is pointless, so it is not sent (O1).

        No route is registered for text.pdf: if the code still requested it,
        respx would fail the test with an unmocked-request error.
        """
        _mock_metadata(act_detail, html=False, pdf=False)

        result = await service.get_details("DU/2024/1", load_content=True)

        assert result.content_status == "unavailable"
        assert not await document_store.is_loaded("DU/2024/1")


def test_no_placeholder_literals_remain_in_src() -> None:
    """The stand-in sentences are gone for good (A7).

    They were English text in an agent-facing field, they made `is_loaded=True`
    a lie, and `search_in_act` matched against them as if they were the act.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[3] / "src"
    banned = ("*No readable content available", "*Content extraction failed")
    offenders = [
        f"{path}: {needle}"
        for path in src.rglob("*.py")
        for needle in banned
        if needle in path.read_text(encoding="utf-8")
    ]

    assert offenders == []
