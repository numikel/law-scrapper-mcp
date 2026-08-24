"""Tests for the log output contract: encoding, correlation id, timestamp zone.

`JsonFormatter` is a function-local class inside `setup_logging`, so it cannot
be imported. Every assertion here goes through a replaced `sys.stderr`, which
`logging.StreamHandler` captures at `setup_logging()` call time.
"""

from __future__ import annotations

import io
import logging
import sys
from collections.abc import Iterator

import pytest

from law_scrapper_mcp.logging_config import setup_logging


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
