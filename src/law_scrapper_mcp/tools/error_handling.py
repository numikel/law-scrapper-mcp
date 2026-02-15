"""Centralized error handling decorator for MCP tools."""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Any

from law_scrapper_mcp.client.exceptions import (
    ActNotFoundError,
    ApiUnavailableError,
    ContentNotAvailableError,
    DocumentNotLoadedError,
    InvalidEliError,
)
from law_scrapper_mcp.models.tool_outputs import EnrichedResponse
from law_scrapper_mcp.services.result_store import ResultSetNotFoundError

logger = logging.getLogger(__name__)

_ERROR_CATEGORIES: dict[type[Exception], str] = {
    ActNotFoundError: "not_found",
    InvalidEliError: "validation",
    DocumentNotLoadedError: "precondition",
    ResultSetNotFoundError: "precondition",
    ContentNotAvailableError: "not_found",
    ApiUnavailableError: "unavailable",
    ValueError: "validation",
    TypeError: "validation",
}


def _classify_error(exc: Exception) -> str:
    """Classify exception into error category."""
    for exc_type, category in _ERROR_CATEGORIES.items():
        if isinstance(exc, exc_type):
            return category
    return "internal"


def handle_tool_errors(
    default_factory: Callable[[Exception, dict[str, Any]], Any],
) -> Callable:
    """Decorator factory for consistent tool error handling.

    Args:
        default_factory: Callable(exception, kwargs) returning default output data on error.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> str:
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                category = _classify_error(e)
                logger.error(
                    "Tool %s failed [%s]: %s",
                    func.__name__,
                    category,
                    e,
                    exc_info=category == "internal",
                )
                error_response = EnrichedResponse(
                    data=default_factory(e, kwargs),
                    error=str(e),
                    metadata={"error_category": category},
                )
                return error_response.model_dump_json()

        return wrapper

    return decorator
