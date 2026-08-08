"""Tests for the shared MCP tool error classification."""

from __future__ import annotations

import json
import logging

import pytest

from law_scrapper_mcp.services.result_store import ResultSetNotFoundError, ResultSetTooLargeError
from law_scrapper_mcp.tools.error_handling import _classify_error, handle_tool_errors


class TestClassifyError:
    """Regresja U1 — ResultSetTooLargeError musi klasyfikować się jak precondition."""

    def test_result_set_not_found_is_precondition(self) -> None:
        assert _classify_error(ResultSetNotFoundError("rs_1")) == "precondition"

    def test_result_set_too_large_is_precondition(self) -> None:
        assert _classify_error(ResultSetTooLargeError("rs_1", 500, 100)) == "precondition"


class TestHandleToolErrorsPublicSurface:
    """U1b — test przez publiczną powierzchnię `handle_tool_errors`, nie przez `_classify_error`.

    `_classify_error` łapie tylko przypadkowe usunięcie wpisu ze słownika.
    Realnym objawem usterki był `exc_info=True` w logach (nadmiarowy traceback
    dla sytuacji, którą klient może naprawić) i `error_category: "internal"`
    widziany przez klienta zamiast `"precondition"` — obie rzeczy sprawdzane tu.
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
        # `handle_tool_errors` przekazuje `exc_info=(category == "internal")`.
        # Stdlib `logging` przechowuje w `LogRecord.exc_info` dosłownie to, co
        # przekazano, gdy jest falsy — czyli literalne `False`, nie `None`
        # (zweryfikowane niezależnie: `Logger.error(..., exc_info=False)` daje
        # `record.exc_info is False`). Funkcjonalny skutek jest ten sam —
        # `Formatter.format` sprawdza `if record.exc_info:` i przy `False`
        # nie dołącza tracebacku — dlatego sprawdzamy falsy, nie `is None`.
        assert not caplog.records[-1].exc_info
