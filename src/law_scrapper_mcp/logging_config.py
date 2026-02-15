"""Logging configuration for Law Scrapper MCP."""

from __future__ import annotations

import logging
import sys
from typing import Literal


def setup_logging(
    level: str = "INFO", format: Literal["text", "json"] = "text"
) -> None:
    """Setup structured logging for the application.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format: Output format ("text" or "json")
    """
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
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

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
