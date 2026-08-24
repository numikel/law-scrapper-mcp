"""Tests for the shared MCP tool error classification."""

from __future__ import annotations

import logging

import httpx
import pytest

from law_scrapper_mcp.client.exceptions import ActNotFoundError, ApiUnavailableError, SejmApiError
from law_scrapper_mcp.services.result_store import ResultSetNotFoundError, ResultSetTooLargeError
from law_scrapper_mcp.tools.error_handling import ToolExecutionError, _classify_error, handle_tool_errors


class TestClassifyError:
    """ResultSetTooLargeError must classify as precondition"""

    def test_result_set_not_found_is_precondition(self) -> None:
        assert _classify_error(ResultSetNotFoundError("rs_1")) == "precondition"

    def test_result_set_too_large_is_precondition(self) -> None:
        assert _classify_error(ResultSetTooLargeError("rs_1", 500, 100)) == "precondition"

    def test_upstream_status_is_not_an_internal_error(self) -> None:
        error = SejmApiError("HTTP 429: too many requests", status_code=429, url="https://api.sejm.gov.pl/eli/acts")
        assert _classify_error(error) == "upstream"

    def test_upstream_timeout_is_not_an_internal_error(self) -> None:
        assert _classify_error(httpx.TimeoutException("timed out")) == "upstream"

    def test_narrow_subclasses_still_win_over_sejm_api_error(self) -> None:
        """`ActNotFoundError` and `ApiUnavailableError` subclass `SejmApiError`."""
        assert _classify_error(ActNotFoundError("DU/2024/1")) == "not_found"
        assert _classify_error(ApiUnavailableError("down", status_code=503, url="u")) == "unavailable"


@pytest.mark.asyncio
class TestHandleToolErrorsPublicSurface:
    """Exercise the public `handle_tool_errors` surface, not `_classify_error`."""

    async def test_upstream_failure_hides_the_response_body(self, caplog: pytest.LogCaptureFixture) -> None:
        secret_body = "<html>internal upstream trace</html>"

        @handle_tool_errors
        async def failing_tool() -> str:
            raise SejmApiError(f"HTTP 500: {secret_body}", status_code=500, url="https://api.sejm.gov.pl/eli/acts")

        with caplog.at_level(logging.ERROR, logger="law_scrapper_mcp.tools.error_handling"):
            with pytest.raises(ToolExecutionError) as exc_info:
                await failing_tool()

        message = str(exc_info.value)
        assert "api.sejm.gov.pl nie odpowiedział poprawnie" in message
        assert secret_body not in message
        assert caplog.records
        assert not caplog.records[-1].exc_info

    async def test_result_set_too_large_is_precondition_without_traceback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        @handle_tool_errors
        async def failing_tool() -> str:
            raise ResultSetTooLargeError("rs_1", 500, 100)

        with caplog.at_level(logging.ERROR, logger="law_scrapper_mcp.tools.error_handling"):
            with pytest.raises(ToolExecutionError, match="rs_1"):
                await failing_tool()

        assert caplog.records
        assert not caplog.records[-1].exc_info

    async def test_validation_failure_keeps_detail_off_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """A validation message is written by the caller and can carry a regex
        pattern or an act title. The public exception still returns it — F13
        is about the durable stderr record, not about the tool response."""
        detail = "wzorzec 'zdrowie|przymusowe' jest nieprawidłowy"

        @handle_tool_errors
        async def failing_tool() -> str:
            raise ValueError(detail)

        with caplog.at_level(logging.DEBUG, logger="law_scrapper_mcp.tools.error_handling"):
            with pytest.raises(ToolExecutionError, match="nieprawidłowy"):
                await failing_tool()

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]

        assert error_records
        assert all(detail not in r.getMessage() for r in error_records)
        assert any("failing_tool" in r.getMessage() for r in error_records)
        assert any(detail in r.getMessage() for r in debug_records)


@pytest.mark.asyncio
async def test_content_too_large_message_survives_sanitization() -> None:
    """The agent must learn that the act exists and where to fetch it.

    Unregistered exceptions fall into the `internal` category, whose public
    message is a fixed generic string — that would swallow both the Polish
    wording and the source URL.
    """
    from law_scrapper_mcp.client.exceptions import ContentTooLargeError
    from law_scrapper_mcp.tools.error_handling import ToolExecutionError, handle_tool_errors

    @handle_tool_errors
    async def failing_tool() -> None:
        raise ContentTooLargeError(
            eli="DU/2024/1",
            size_bytes=9_000_000,
            limit_bytes=5_242_880,
            pdf_url="https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.pdf",
        )

    with pytest.raises(ToolExecutionError) as excinfo:
        await failing_tool()

    message = str(excinfo.value)
    assert "DU/2024/1" in message
    assert "https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.pdf" in message
    assert "przekracza limit" in message
    assert "wewnętrzny błąd" not in message


def test_content_too_large_is_classified_as_precondition() -> None:
    from law_scrapper_mcp.client.exceptions import ContentTooLargeError
    from law_scrapper_mcp.tools.error_handling import _classify_error

    error = ContentTooLargeError(
        eli="DU/2024/1",
        size_bytes=9_000_000,
        limit_bytes=5_242_880,
        pdf_url="https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.pdf",
    )

    assert _classify_error(error) == "precondition"
