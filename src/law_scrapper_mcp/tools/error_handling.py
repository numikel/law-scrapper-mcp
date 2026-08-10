"""Centralized error handling for MCP tools."""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

from law_scrapper_mcp.client.exceptions import (
    ActNotFoundError,
    ApiUnavailableError,
    ContentNotAvailableError,
    DocumentNotLoadedError,
    InvalidEliError,
)
from law_scrapper_mcp.services.result_store import ResultSetNotFoundError, ResultSetTooLargeError

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")

_ERROR_CATEGORIES: dict[type[Exception], str] = {
    ActNotFoundError: "not_found",
    InvalidEliError: "validation",
    DocumentNotLoadedError: "precondition",
    ResultSetNotFoundError: "precondition",
    ResultSetTooLargeError: "precondition",
    ContentNotAvailableError: "not_found",
    ApiUnavailableError: "unavailable",
    ValueError: "validation",
    TypeError: "validation",
}


class ToolExecutionError(Exception):
    """Public, sanitized tool execution failure."""


def _classify_error(exc: Exception) -> str:
    for exc_type, category in _ERROR_CATEGORIES.items():
        if isinstance(exc, exc_type):
            return category
    return "internal"


def _public_message(exc: Exception, category: str) -> str:
    if category == "internal":
        return "Wystąpił wewnętrzny błąd narzędzia. Spróbuj ponownie."
    return str(exc)


def handle_tool_errors(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:  # noqa: UP047
    """Log tool failures and re-raise a sanitized ordinary exception."""

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            category = _classify_error(exc)
            logger.error(
                "Tool %s failed [%s]: %s",
                func.__name__,
                category,
                exc,
                exc_info=category == "internal",
            )
            raise ToolExecutionError(_public_message(exc, category)) from exc

    return wrapper
