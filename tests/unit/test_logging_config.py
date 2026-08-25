"""Tests for the log output contract: encoding, correlation id, timestamp zone.

`JsonFormatter` is a function-local class inside `setup_logging`, so it cannot
be imported. Every assertion here goes through a replaced `sys.stderr`, which
`logging.StreamHandler` captures at `setup_logging()` call time.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import sys
from collections.abc import Iterator

import pytest

from law_scrapper_mcp.logging_config import DEFAULT_REQUEST_ID, request_id_var, setup_logging
from law_scrapper_mcp.tools.error_handling import handle_tool_errors


class RecordingStream(io.StringIO):
    """A stderr stand-in that records how `setup_logging` reconfigured it."""

    def __init__(self) -> None:
        super().__init__()
        self.reconfigured: dict[str, str] | None = None

    def reconfigure(self, *, encoding: str, errors: str) -> None:
        self.reconfigured = {"encoding": encoding, "errors": errors}


@pytest.fixture(autouse=True)
def restore_logging_state() -> Iterator[None]:
    """Undo the global mutation `setup_logging` performs.

    It strips every root handler, including the one pytest's `caplog` relies
    on, so without this fixture the tests below would break unrelated tests
    that run later in the same session.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_root_level = root.level
    app_logger = logging.getLogger("law_scrapper_mcp")
    saved_app_level = app_logger.level

    yield

    root.handlers[:] = saved_handlers
    root.setLevel(saved_root_level)
    app_logger.setLevel(saved_app_level)


def test_setup_logging_forces_utf8_on_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = RecordingStream()
    monkeypatch.setattr(sys, "stderr", stream)

    setup_logging(level="INFO", format="text")

    assert stream.reconfigured == {"encoding": "utf-8", "errors": "backslashreplace"}


def test_setup_logging_survives_a_stream_without_reconfigure(monkeypatch: pytest.MonkeyPatch) -> None:
    """An embedded runtime or a replaced stream must not break server startup."""
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    setup_logging(level="INFO", format="text")

    logging.getLogger("law_scrapper_mcp.test").info("startup")


def test_setup_logging_never_touches_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that stdout is never reconfigured (STDIO transport framing must be untouched)."""
    stderr_stream = RecordingStream()
    stdout_stream = RecordingStream()
    monkeypatch.setattr(sys, "stderr", stderr_stream)
    monkeypatch.setattr(sys, "stdout", stdout_stream)

    setup_logging(level="INFO", format="text")

    assert stdout_stream.reconfigured is None


def test_json_record_carries_request_id_outside_a_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    setup_logging(level="INFO", format="json")

    logging.getLogger("law_scrapper_mcp.test").info("lifespan started")

    payload = json.loads(stream.getvalue().strip())
    assert payload["request_id"] == "lifespan"


@pytest.mark.asyncio
async def test_concurrent_tool_calls_log_distinct_request_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two asyncio tasks must not see each other's correlation id.

    Each task runs in a copy of the context, so `.set()` inside one is
    invisible to the other and to the caller.
    """
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    setup_logging(level="INFO", format="json")
    log = logging.getLogger("law_scrapper_mcp.test")

    @handle_tool_errors
    async def fake_tool(name: str) -> str:
        await asyncio.sleep(0)  # force interleaving
        log.info("inside %s", name)
        return name

    await asyncio.gather(fake_tool("a"), fake_tool("b"))

    ids = [json.loads(line)["request_id"] for line in stream.getvalue().splitlines() if line.strip()]
    assert len(ids) == 2
    assert ids[0] != ids[1]
    assert all(re.fullmatch(r"[0-9a-f]{8}", value) for value in ids)
    assert request_id_var.get() == "lifespan"


def test_text_format_survives_a_record_without_the_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """The filter supplies `request_id`; `defaults` covers whatever escapes it.

    A record formatted by a handler this project did not configure would
    otherwise raise `KeyError` and be swallowed into a crash dump.
    """
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    setup_logging(level="INFO", format="text")
    formatter = logging.getLogger().handlers[0].formatter
    assert formatter is not None

    record = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, "started", None, None)

    assert f"[{DEFAULT_REQUEST_ID}]" in formatter.format(record)


def test_polish_diacritics_are_not_escaped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Escaped diacritics would make `grep` useless on Polish error messages."""
    message = "Nie znaleziono aktu — żółć, ląd, ważne"

    json_stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", json_stream)
    setup_logging(level="INFO", format="json")
    logging.getLogger("law_scrapper_mcp.test").info(message)
    rendered = json_stream.getvalue()
    assert message in rendered
    assert "\\u017c" not in rendered

    text_stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", text_stream)
    setup_logging(level="INFO", format="text")
    logging.getLogger("law_scrapper_mcp.test").info(message)
    rendered_text = text_stream.getvalue()
    # Any formatter failure — a `request_id` the record lacks, a broken format
    # string — is swallowed by `logging.Handler.handleError()`, which echoes
    # the record's message back into this same stream as part of its crash
    # dump. A bare `message in rendered_text` assertion would pass on output
    # that is broken end to end, so rule the crash dump out first.
    assert "--- Logging error ---" not in rendered_text
    assert "law_scrapper_mcp.test - [lifespan] - INFO" in rendered_text
    assert message in rendered_text


def test_json_timestamp_is_explicit_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    """The timestamp field must be timezone-aware and explicitly marked as UTC.

    A naive timestamp (without +00:00 or Z) is ambiguous: log aggregators
    default to local time, which in Europe/Warsaw would show events two hours
    earlier than correlated upstream events. The suffix +00:00 is the ISO 8601
    way to explicitly mark UTC.
    """
    from datetime import datetime

    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    setup_logging(level="INFO", format="json")

    logging.getLogger("law_scrapper_mcp.test").info("test message")

    payload = json.loads(stream.getvalue().strip())
    timestamp_str = payload["timestamp"]

    # Must end with +00:00 (ISO 8601 explicit UTC marker)
    assert timestamp_str.endswith("+00:00"), f"Expected timestamp to end with +00:00, got {timestamp_str}"

    # Must parse as timezone-aware datetime
    parsed = datetime.fromisoformat(timestamp_str)
    assert parsed.tzinfo is not None, f"Timestamp must be timezone-aware, got {parsed}"
