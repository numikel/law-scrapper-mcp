"""Centralized error handling for MCP tools."""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar
from uuid import uuid4

import httpx

from law_scrapper_mcp.client.exceptions import (
    ActNotFoundError,
    ApiUnavailableError,
    ContentNotAvailableError,
    ContentTooLargeError,
    DocumentNotLoadedError,
    InvalidEliError,
    SejmApiError,
)
from law_scrapper_mcp.logging_config import request_id_var
from law_scrapper_mcp.services.result_store import ResultSetNotFoundError, ResultSetTooLargeError

logger = logging.getLogger(__name__)
P = ParamSpec("P")
R = TypeVar("R")

# Order matters: `_classify_error` returns the first isinstance match, so the
# narrow subclasses of `SejmApiError` must precede it.
_ERROR_CATEGORIES: dict[type[Exception], str] = {
    ActNotFoundError: "not_found",
    InvalidEliError: "validation",
    DocumentNotLoadedError: "precondition",
    ResultSetNotFoundError: "precondition",
    ResultSetTooLargeError: "precondition",
    ContentNotAvailableError: "not_found",
    ContentTooLargeError: "precondition",
    ApiUnavailableError: "unavailable",
    SejmApiError: "upstream",
    httpx.TimeoutException: "upstream",
    ValueError: "validation",
    TypeError: "validation",
}

# `SejmApiError` embeds the upstream response body, so this category must never
# fall through to `str(exc)`.
_UPSTREAM_MESSAGE = "Serwis api.sejm.gov.pl nie odpowiedział poprawnie. Spróbuj ponownie za chwilę."


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
    if category == "upstream":
        return _UPSTREAM_MESSAGE
    return str(exc)


def handle_tool_errors(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:  # noqa: UP047
    """Log tool failures and re-raise a sanitized ordinary exception."""

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        # One id per tool call. The decorator wraps all 13 tools and behaves
        # identically on STDIO and Streamable HTTP, so this is the single
        # place both transports need.
        request_id_var.set(uuid4().hex[:8])
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
