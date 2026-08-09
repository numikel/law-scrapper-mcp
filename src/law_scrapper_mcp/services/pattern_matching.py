"""Compile client-supplied regex patterns.
"""

from __future__ import annotations

from typing import Any

import re2

from law_scrapper_mcp.config import (
    MAX_PATTERN_LENGTH_CEILING,
    MAX_PATTERN_LENGTH_FLOOR,
)

RE2_MAX_MEM_BYTES = 2 * 1024 * 1024

CompiledPattern = Any

SUPPORTED_SYNTAX_HINT = (
    "Obsługiwany podzbiór składni: alternatywa (a|b), klasy znaków "
    r"([a-z], \d, \p{L}, [[:alpha:]]), kwantyfikatory (*, +, ?, {n,m}), "
    r"kotwice (^, $), grupy nieprzechwytujące. Lookaround ((?=...), (?<=...), "
    r"(?!...)) oraz backreferencje (\1) nie są obsługiwane."
)


class PatternValidationError(ValueError):
    """Raised when a pattern is rejected before matching runs.

    Subclasses ValueError because the tools layer treats ValueError as a
    user-input error rather than a server failure.
    """


def _build_options() -> Any:
    """Build RE2 options shared by all client-supplied patterns."""
    options = re2.Options()
    # RE2 does not expose a constant equivalent to re.IGNORECASE.
    options.case_sensitive = False
    options.log_errors = False
    options.max_mem = RE2_MAX_MEM_BYTES
    return options


def _is_pattern_too_large(e: re2.error) -> bool:
    """Detect a compilation memory-budget overrun (`RE2_MAX_MEM_BYTES`).

    RE2 signals this with the same exception type as a syntax error (`re2.error`),
    so the message text must be inspected to avoid suggesting a syntax fix when
    the only problem is pattern complexit.
    """
    if not e.args:
        return False
    detail = e.args[0]
    return isinstance(detail, bytes) and b"pattern too large" in detail


def _re2_error_detail(e: re2.error) -> str:
    """Extract a readable syntax-error description from a `re2.error`.

    The C++ RE2 layer returns the message as `bytes` (`e.args[0]`) because the
    pattern is UTF-8-encoded before leaving Python — interpolating that raw
    value would produce Polish-character reprs like `b'...\\xc5\\xbc...'` in the
    error message, which confuses the language-model client of this tool.
    Decode with `errors="replace"` so error formatting never raises on its own.
    """
    if not e.args:
        return str(e)
    detail = e.args[0]
    if isinstance(detail, bytes):
        return detail.decode("utf-8", "replace")
    return str(detail)


def _too_complex_message() -> str:
    return (
        "Wzorzec jest zbyt złożony: przekracza budżet pamięci kompilacji silnika "
        "wyszukiwania. Uprość wzorzec — zmniejsz liczbę alternatyw albo zawęź "
        "kwantyfikatory takie jak {n,m}."
    )


def _too_long_message(actual_length: int, max_length: int, limit_was_clamped: bool) -> str:
    base = f"Wzorzec jest za długi: {actual_length} znaków, limit wynosi {max_length}."
    if limit_was_clamped:
        return (
            f"{base} Limit efektywny {max_length} wynika z przycięcia konfiguracji "
            f"LAW_MCP_MAX_PATTERN_LENGTH do dozwolonego zakresu "
            f"{MAX_PATTERN_LENGTH_FLOOR}-{MAX_PATTERN_LENGTH_CEILING}."
        )
    return f"{base} Skróć wzorzec lub zawęź listę alternatyw."


def compile_pattern(
    pattern: str,
    *,
    max_length: int,
    limit_was_clamped: bool = False,
) -> CompiledPattern:
    """Validate and compile a client-supplied pattern.

    Args:
        pattern: Raw pattern passed by the tool caller.
        max_length: Effective pattern length limit (after clamping to the range).
        limit_was_clamped: Whether `max_length` came from a clamped configuration.
            Affects only the error message text (D3.1).

    Raises:
        PatternValidationError: Pattern exceeds the length limit, uses syntax
            outside the RE2-supported subset, exceeds the compilation memory
            budget (`RE2_MAX_MEM_BYTES`), or contains characters that cannot be
            encoded as UTF-8 — e.g. a lone UTF-16 surrogate (U1).
    """
    if len(pattern) > max_length:
        raise PatternValidationError(_too_long_message(len(pattern), max_length, limit_was_clamped))

    try:
        return re2.compile(pattern, _build_options())
    except (re2.error, UnicodeError) as e:
        if isinstance(e, re2.error):
            if _is_pattern_too_large(e):
                raise PatternValidationError(_too_complex_message()) from e
            detail = _re2_error_detail(e)
        else:
            detail = str(e)
        raise PatternValidationError(
            f"Wzorzec nie jest obsługiwany przez silnik wyszukiwania: {detail}. {SUPPORTED_SYNTAX_HINT}"
        ) from e
