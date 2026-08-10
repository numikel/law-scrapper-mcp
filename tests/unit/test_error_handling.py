"""Tests for the shared MCP tool error classification."""

from __future__ import annotations

import logging

import pytest

from law_scrapper_mcp.services.result_store import ResultSetNotFoundError, ResultSetTooLargeError
from law_scrapper_mcp.tools.error_handling import ToolExecutionError, _classify_error, handle_tool_errors

pytestmark = pytest.mark.asyncio


class TestClassifyError:
    """ResultSetTooLargeError must classify as precondition"""

    def test_result_set_not_found_is_precondition(self) -> None:
        assert _classify_error(ResultSetNotFoundError("rs_1")) == "precondition"

    def test_result_set_too_large_is_precondition(self) -> None:
        assert _classify_error(ResultSetTooLargeError("rs_1", 500, 100)) == "precondition"


class TestHandleToolErrorsPublicSurface:
    """Exercise the public `handle_tool_errors` surface, not `_classify_error`."""

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
