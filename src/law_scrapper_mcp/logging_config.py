"""Logging configuration for Law Scrapper MCP."""

from __future__ import annotations

import contextvars
import logging
import sys
from typing import Literal

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="lifespan")
"""Correlation id of the tool call in flight.

The default is a value, not a sentinel: records emitted from `lifespan`,
background eviction, or process startup carry `"lifespan"` without any code
setting it, which keeps the JSON key set stable for log aggregation.
"""


class RequestIdFilter(logging.Filter):
    """Stamp every record with the current correlation id.

    Attached to the handler rather than to the `law_scrapper_mcp` logger on
    purpose: records from uvicorn and httpx know nothing about this mechanism,
    and the text format references `%(request_id)s` on every record it renders.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


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
                    "request_id": getattr(record, "request_id", "lifespan"),
                    "message": record.getMessage(),
                }
                if record.exc_info:
                    log_data["exception"] = self.formatException(record.exc_info)
                return json.dumps(log_data, ensure_ascii=False)

        formatter: logging.Formatter = JsonFormatter()
    else:
        # Text format for development
        formatter = logging.Formatter("%(asctime)s - %(name)s - [%(request_id)s] - %(levelname)s - %(message)s")

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add stderr handler
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(log_level)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Configure law_scrapper_mcp logger
    app_logger = logging.getLogger("law_scrapper_mcp")
    app_logger.setLevel(log_level)
    app_logger.propagate = True
