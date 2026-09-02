"""Byte budget on document downloads, enforced while the body is still streaming (#19).

The post-hoc size checks in `ActService` bound what gets converted, but by then the
whole body has already been materialised in memory. These tests drive a chunked byte
stream that records how much of it the client pulled, so "refused before
materialising" is asserted directly rather than inferred.
"""

from __future__ import annotations

import gzip
import random
from collections.abc import AsyncGenerator, AsyncIterator

import httpx
import pytest
import pytest_asyncio
import respx

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.circuit_breaker import CircuitBreaker
from law_scrapper_mcp.client.exceptions import ActNotFoundError, ContentTooLargeError, SejmApiError
from law_scrapper_mcp.client.sejm_client import SejmApiClient

pytestmark = pytest.mark.asyncio

HTML_URL = "https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.html"
HTML_PATH = "acts/DU/2024/1/text.html"
PDF_URL = "https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.pdf"
PDF_PATH = "acts/DU/2024/1/text.pdf"
LIMIT = 1024


class RecordingStream(httpx.AsyncByteStream):
    """A response body served in chunks that counts how many were pulled."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.pulled = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.pulled += 1
            yield chunk


@pytest.fixture
def breaker() -> CircuitBreaker:
    return CircuitBreaker(failure_threshold=5, recovery_timeout=60.0, half_open_max_calls=3)


@pytest_asyncio.fixture
async def client(breaker: CircuitBreaker) -> AsyncGenerator[SejmApiClient]:
    api = SejmApiClient(cache=TTLCache(max_entries=10), circuit_breaker=breaker, max_attempts=3)
    await api.start()
    yield api
    await api.close()


@respx.mock
async def test_a_body_past_the_budget_is_refused_before_it_is_materialised(client: SejmApiClient) -> None:
    """Four chunks of 512 B against a 1024 B budget: the third one crosses it, the fourth is never pulled."""
    stream = RecordingStream([b"x" * 512] * 4)
    respx.get(PDF_URL).mock(return_value=httpx.Response(200, stream=stream))

    with pytest.raises(ContentTooLargeError) as refused:
        await client.get_bytes(PDF_PATH, max_bytes=LIMIT)

    assert stream.pulled == 3
    assert refused.value.limit_bytes == LIMIT
    assert refused.value.size_bytes == 1536
    assert "przekracza limit" in str(refused.value)


@respx.mock
async def test_a_declared_length_past_the_budget_is_refused_without_reading(client: SejmApiClient) -> None:
    """`Content-Length` is the cheap answer: nothing of the body needs to be read to refuse it."""
    stream = RecordingStream([b"x" * 512] * 4)
    respx.get(PDF_URL).mock(
        return_value=httpx.Response(200, stream=stream, headers={"Content-Length": str(LIMIT + 1)}),
    )

    with pytest.raises(ContentTooLargeError) as refused:
        await client.get_bytes(PDF_PATH, max_bytes=LIMIT)

    assert stream.pulled == 0
    assert refused.value.size_bytes == LIMIT + 1


@respx.mock
async def test_a_body_exactly_at_the_budget_passes(client: SejmApiClient) -> None:
    """The budget is a maximum, not a strict bound — a document of exactly the limit is fine."""
    body = b"x" * LIMIT
    respx.get(PDF_URL).mock(return_value=httpx.Response(200, stream=RecordingStream([body[:512], body[512:]])))

    assert await client.get_bytes(PDF_PATH, max_bytes=LIMIT) == body


@respx.mock
async def test_a_budgeted_text_download_still_honours_the_response_charset(client: SejmApiClient) -> None:
    """Decoding must not silently switch to UTF-8 just because the body arrived in chunks."""
    text = "<p>Zażółć gęślą jaźń</p>"
    respx.get(HTML_URL).mock(
        return_value=httpx.Response(
            200,
            stream=RecordingStream([text.encode("iso-8859-2")]),
            headers={"Content-Type": "text/html; charset=iso-8859-2"},
        )
    )

    assert await client.get_text(HTML_PATH, max_bytes=LIMIT) == text


@respx.mock
async def test_a_budget_refusal_is_neither_retried_nor_a_breaker_failure(
    client: SejmApiClient, breaker: CircuitBreaker
) -> None:
    """Our own policy speaking, not the wire: a second attempt would only re-download the same body."""
    route = respx.get(PDF_URL).mock(return_value=httpx.Response(200, stream=RecordingStream([b"x" * 2048])))

    with pytest.raises(ContentTooLargeError):
        await client.get_bytes(PDF_PATH, max_bytes=LIMIT)

    assert route.call_count == 1
    assert breaker.failure_count == 0


@respx.mock
async def test_an_error_status_under_a_budget_is_translated_as_before(client: SejmApiClient) -> None:
    """Streaming must not change what a 404 or a 4xx body turns into."""
    respx.get(PDF_URL).mock(return_value=httpx.Response(404))
    respx.get(HTML_URL).mock(return_value=httpx.Response(400, text="zły parametr"))

    with pytest.raises(ActNotFoundError):
        await client.get_bytes(PDF_PATH, max_bytes=LIMIT)
    with pytest.raises(SejmApiError) as rejected:
        await client.get_text(HTML_PATH, max_bytes=LIMIT)

    assert "zły parametr" in str(rejected.value)


@respx.mock
async def test_without_a_budget_the_whole_body_is_read(client: SejmApiClient) -> None:
    """`max_bytes=None` keeps the previous contract: callers that set no budget get everything."""
    stream = RecordingStream([b"x" * 512] * 4)
    respx.get(PDF_URL).mock(return_value=httpx.Response(200, stream=stream))

    assert len(await client.get_bytes(PDF_PATH)) == 2048
    assert stream.pulled == 4


@respx.mock
async def test_a_compressed_body_is_decoded_once_not_twice(client: SejmApiClient) -> None:
    """httpx already decoded the chunks, so the rebuilt response must not claim they are still gzipped.

    Carrying `Content-Encoding` onto the materialised response made httpx run the
    codec a second time over plain text and raise `DecodingError`, which the
    translating layer turned into `SejmApiError` — every budgeted act download
    failing against any upstream that compresses, which is every upstream httpx
    advertises `Accept-Encoding` to.
    """
    text = "<p>Zażółć gęślą jaźń</p>"
    body = gzip.compress(text.encode("utf-8"))
    respx.get(HTML_URL).mock(
        return_value=httpx.Response(
            200,
            stream=RecordingStream([body]),
            headers={"Content-Encoding": "gzip", "Content-Type": "text/html; charset=utf-8"},
        )
    )

    assert await client.get_text(HTML_PATH, max_bytes=LIMIT) == text


@respx.mock
async def test_a_compressed_declared_length_does_not_settle_the_budget(client: SejmApiClient) -> None:
    """`Content-Length` counts wire bytes; the budget counts decoded ones.

    A body that compresses to under the budget and expands past it must still be
    refused, and by the stream check — the declared length cannot answer for it.
    """
    body = gzip.compress(b"x" * (LIMIT * 4))
    assert len(body) < LIMIT, "the fixture only tests what it means to if the compressed body fits"
    respx.get(PDF_URL).mock(
        return_value=httpx.Response(
            200,
            stream=RecordingStream([body]),
            headers={"Content-Encoding": "gzip", "Content-Length": str(len(body))},
        )
    )

    with pytest.raises(ContentTooLargeError) as refused:
        await client.get_bytes(PDF_PATH, max_bytes=LIMIT)

    assert refused.value.exact is False


@respx.mock
async def test_a_redirect_without_a_location_is_refused_like_the_unbudgeted_path(client: SejmApiClient) -> None:
    """`raise_for_status()` rejects every non-2xx, so the budgeted path must too.

    Gating on `is_error` (4xx/5xx only) let a 3xx with nothing to follow through
    to a caller as an empty act body instead of an error.
    """
    respx.get(HTML_URL).mock(return_value=httpx.Response(300, text=""))

    with pytest.raises(SejmApiError):
        await client.get_text(HTML_PATH, max_bytes=LIMIT)


@respx.mock
async def test_a_compressed_wire_length_over_the_budget_does_not_refuse_a_body_that_fits(
    client: SejmApiClient,
) -> None:
    """The declared length counts wire bytes, so it cannot refuse on the store's behalf.

    Incompressible content encodes to slightly more than it decodes to. Reading
    `Content-Length` as the document's size then refused a body that fits the
    budget the store actually enforces — and quoted the wire figure as `exact`.
    """
    payload = random.Random(0).randbytes(LIMIT)
    body = gzip.compress(payload)
    assert len(payload) <= LIMIT < len(body), "the fixture only tests what it means to if encoding grows the body"
    respx.get(PDF_URL).mock(
        return_value=httpx.Response(
            200,
            stream=RecordingStream([body]),
            headers={"Content-Encoding": "gzip", "Content-Length": str(len(body))},
        )
    )

    assert await client.get_bytes(PDF_PATH, max_bytes=LIMIT) == payload
