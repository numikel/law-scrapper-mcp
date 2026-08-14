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
