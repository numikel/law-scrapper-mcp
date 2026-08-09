"""Tests for the shared MCP tool error classification."""

from __future__ import annotations

import json
import logging

import pytest

from law_scrapper_mcp.services.result_store import ResultSetNotFoundError, ResultSetTooLargeError
from law_scrapper_mcp.tools.error_handling import _classify_error, handle_tool_errors


class TestClassifyError:
    """ResultSetTooLargeError must classify as precondition"""

    def test_result_set_not_found_is_precondition(self) -> None:
        assert _classify_error(ResultSetNotFoundError("rs_1")) == "precondition"

    def test_result_set_too_large_is_precondition(self) -> None:
        assert _classify_error(ResultSetTooLargeError("rs_1", 500, 100)) == "precondition"


class TestHandleToolErrorsPublicSurface:
    """Exercise the public `handle_tool_errors` surface, not `_classify_error`.

    `_classify_error` alone only catches accidental removal of a dict entry.
    The real failure mode was `exc_info=True` in logs (excess traceback for a
    client-fixable situation) and `error_category: "internal"` seen by the
    client instead of `"precondition"` — both are checked here.
    """

    async def test_result_set_too_large_is_precondition_without_traceback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        @handle_tool_errors(default_factory=lambda e, kw: {})
        async def failing_tool() -> str:
            raise ResultSetTooLargeError("rs_1", 500, 100)

        with caplog.at_level(logging.ERROR, logger="law_scrapper_mcp.tools.error_handling"):
            out = await failing_tool()

        payload = json.loads(out)
        assert payload["metadata"]["error_category"] == "precondition"

        assert caplog.records
        # `handle_tool_errors` passes `exc_info=(category == "internal")`.
        # Stdlib `logging` stores in `LogRecord.exc_info` exactly what was
        # passed when falsy — a literal `False`, not `None` (independently
        # verified: `Logger.error(..., exc_info=False)` yields
        # `record.exc_info is False`). The functional outcome is the same —
        # `Formatter.format` checks `if record.exc_info:` and with `False`
        # skips the traceback — so we assert falsy, not `is None`.
        assert not caplog.records[-1].exc_info
