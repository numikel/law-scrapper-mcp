"""Logging configuration for Law Scrapper MCP."""

from __future__ import annotations

import logging
import sys
from typing import Literal


def _force_utf8_stderr() -> None:
    """Make stderr accept non-ASCII records regardless of platform default.

    Windows consoles default to cp1252, which mangles Polish diacritics. The
    call is guarded twice: `reconfigure` is absent on replaced streams (tests,
    embedded runtimes), and `errors="backslashreplace"` keeps a stubborn
    stream from turning a log line into a `UnicodeEncodeError`. Logging must
    never be the reason the server fails to start.

    Only stderr is touched: the STDIO transport carries the MCP protocol over
    stdout, which must keep its own configuration.
    """
    reconfigure = getattr(sys.stderr, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        # Any exception (TypeError from incompatible stream, ValueError from
        # detached/closed stream, etc.): logging still works, only the encoding
        # guarantee is lost. The absolute invariant "logging must never break
        # startup" takes precedence over encoding safety.
        return


def setup_logging(level: str = "INFO", format: Literal["text", "json"] = "text") -> None:
    """Setup structured logging for the application.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format: Output format ("text" or "json")
    """
    _force_utf8_stderr()

    # Convert level string to logging constant
    log_level = getattr(logging, level.upper(), logging.INFO)

    if format == "json":
        # JSON format for production
        import json
        from datetime import datetime

        class JsonFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                log_data = {
                    "timestamp": datetime.utcnow().isoformat(),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }
                if record.exc_info:
                    log_data["exception"] = self.formatException(record.exc_info)
                return json.dumps(log_data)

        formatter: logging.Formatter = JsonFormatter()
    else:
        # Text format for development
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add stderr handler
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(log_level)
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Configure law_scrapper_mcp logger
    app_logger = logging.getLogger("law_scrapper_mcp")
    app_logger.setLevel(log_level)
    app_logger.propagate = True
