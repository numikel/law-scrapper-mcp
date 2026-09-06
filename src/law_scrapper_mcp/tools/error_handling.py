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
from law_scrapper_mcp.config import settings
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
# fall through to `str(exc)`. The retry advice lives in `_CATEGORY_GUIDANCE`, not
# here, so that every category's closing sentence is written in one place.
_UPSTREAM_MESSAGE = "Serwis api.sejm.gov.pl nie odpowiedział poprawnie."

_INTERNAL_MESSAGE = "Wystąpił wewnętrzny błąd narzędzia."

# One fixed sentence per category, appended to every public message (D4). Fixed,
# because it enters the caller's context on every failure (O7); a sentence rather
# than the category name, because the name would read as leaked internals and
# would freeze an undocumented protocol in a free-text field.
_CATEGORY_GUIDANCE: dict[str, str] = {
    "not_found": "Ten zasób nie występuje w rejestrze — sprawdź identyfikator przed ponowieniem.",
    "validation": "Popraw parametr wywołania i spróbuj ponownie.",
    "precondition": "Wykonaj najpierw krok wymagany przez to narzędzie.",
    "unavailable": "Ponów wywołanie za chwilę.",
    "upstream": "Ponów wywołanie za chwilę.",
    "internal": "Ponów wywołanie; jeśli błąd wraca, zgłoś go opiekunowi serwera.",
}

# Categories whose exception text this project did not author, and which can
# therefore echo back what the caller submitted. `validation` messages quote
# caller input directly (a regex pattern, an act title); `upstream` messages
# carry the Sejm API response body, which a 4xx can fill with the parameters of
# the rejected request. Both are redacted from ERROR and stay recoverable at
# DEBUG. `TypeError` also lands in `validation` even though it usually signals
# an internal bug rather than caller input — narrowing that classification is
# out of scope here, and redacting it costs only log detail.
_REDACTED_DETAIL_CATEGORIES = frozenset({"validation", "upstream"})


class ToolExecutionError(Exception):
    """Public, sanitized tool execution failure."""


def _classify_error(exc: Exception) -> str:
    for exc_type, category in _ERROR_CATEGORIES.items():
        if isinstance(exc, exc_type):
            return category
    return "internal"


def _status_suffix(exc: Exception) -> str:
    """Render the HTTP status of an upstream failure, if it carries one.

    The status is the one part of an upstream failure that cannot echo back
    submitted parameters, so it stays on ERROR while the body drops to DEBUG.
    """
    status = getattr(exc, "status_code", None)
    return "" if status is None else f" (HTTP {status})"


_TRUNCATION_SUFFIX = " […] (komunikat przycięty)"


def _truncate(message: str) -> str:
    """Bound a message this project did not author.

    The cut is announced rather than silent: a model reading a sentence that
    simply stops has no way to tell truncation from the real end of the text,
    and would draw conclusions from a fragment (D8).
    """
    limit = settings.error_message_max_chars
    if len(message) <= limit:
        return message
    return message[: limit - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX


def _public_message(exc: Exception, category: str) -> str:
    if category == "internal":
        body = _INTERNAL_MESSAGE
    elif category == "upstream":
        body = _UPSTREAM_MESSAGE
    else:
        body = _truncate(str(exc))
    # A truncated body already ends on the announcement suffix, which is itself
    # a complete parenthetical — appending a period there would split it in two.
    if body and not body.endswith(_TRUNCATION_SUFFIX) and body[-1] not in ".!?…":
        body += "."
    return f"{body} {_CATEGORY_GUIDANCE[category]}".strip()


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
            if category in _REDACTED_DETAIL_CATEGORIES:
                logger.error("Tool %s failed [%s]%s", func.__name__, category, _status_suffix(exc))
                logger.debug("Tool %s failure detail [%s]: %s", func.__name__, category, exc)
            else:
                logger.error(
                    "Tool %s failed [%s]: %s",
                    func.__name__,
                    category,
                    exc,
                    exc_info=category == "internal",
                )
            raise ToolExecutionError(_public_message(exc, category)) from exc

    return wrapper
