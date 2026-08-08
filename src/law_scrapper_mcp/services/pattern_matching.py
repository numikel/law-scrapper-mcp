"""Kompilacja wzorców regex dostarczonych przez klienta.

Jedyne miejsce w projekcie, które kompiluje wzorzec pochodzący od wywołującego
narzędzie MCP. Silnikiem jest RE2 (`google-re2`), który gwarantuje złożoność
liniową względem długości wejścia — katastroficzny nawrót jest tu niemożliwy
strukturalnie, a nie mitygowany limitami czy timeoutem.

Kontekst: ustalenie F01 audytu `docs/mcp-audit-2026-08-06.md`, decyzje D1-D3 i D5
specyfikacji `docs/superpowers/specs/2026-08-07-klaster-1.md`.
"""

from __future__ import annotations

from typing import Any

import re2

from law_scrapper_mcp.config import (
    MAX_PATTERN_LENGTH_CEILING,
    MAX_PATTERN_LENGTH_FLOOR,
)

# Parametr klasy "bezpieczeństwo" (D3): stała w kodzie, celowo NIE konfigurowalna
# przez zmienną środowiskową. Domyślna wartość biblioteki RE2 to również 8 MiB
# (`re2.Options().max_mem == 8388608`), więc ta stała musi być NIŻSZA od
# domyślnej, żeby cokolwiek realnie ograniczać. Powód: skompilowane wzorce
# trafiają do globalnego `functools.lru_cache(maxsize=128)` wewnątrz pakietu
# `google-re2` — przy limicie 8 MiB sufit tego cache'u to ~1 GiB pamięci
# utrzymywanej przez legalne, niezłośliwe wzorce klienta. Przy 2 MiB sufit
# spada do ~256 MiB.
#
# 2 MiB (a nie 1 MiB) dobrane empirycznie: `\p{L}` to ogromna klasa Unicode,
# więc powtórzenia tej klasy są dla RE2 najdroższe pamięciowo ze wszystkich
# konstrukcji w obsługiwanym podzbiorze składni. Przy 1 MiB granica
# `\p{L}{0,n}` leżała przy n≈55 — odrzucała niewinne wzorce w stylu
# `\p{L}{0,200}(?:zdrow|apteka)\p{L}{0,200}`. Przy 2 MiB granica dla
# `\p{L}{0,n}` to n≈111 (zmierzone: n=111 kompiluje się, n=112 już nie).
# Wzorce klasy `.`, `\w`, `[a-ząćęłńóśźż]` uderzają najpierw we własny limit
# powtórzeń RE2 (1000), więc dla nich obie wartości max_mem są równoważne.
RE2_MAX_MEM_BYTES = 2 * 1024 * 1024

# Skompilowany wzorzec RE2 (`re2._Regexp`). Pakiet `google-re2` nie dostarcza
# stubów typów, więc alias jest jawnym `Any` zamiast fikcyjnej nazwy typu.
CompiledPattern = Any

SUPPORTED_SYNTAX_HINT = (
    "Obsługiwany podzbiór składni: alternatywa (a|b), klasy znaków "
    r"([a-z], \d, \p{L}, [[:alpha:]]), kwantyfikatory (*, +, ?, {n,m}), "
    r"kotwice (^, $), grupy nieprzechwytujące. Lookaround ((?=...), (?<=...), "
    r"(?!...)) oraz backreferencje (\1) nie są obsługiwane."
)


class PatternValidationError(ValueError):
    """Wzorzec odrzucony przed uruchomieniem dopasowania.

    Dziedziczy po ValueError, ponieważ warstwa narzędzi traktuje ValueError jako
    błąd wejścia użytkownika, a nie awarię serwera.
    """


def _build_options() -> Any:
    """Zbuduj opcje RE2 wspólne dla wszystkich wzorców od klienta."""
    options = re2.Options()
    # RE2 nie eksponuje stałej odpowiadającej re.IGNORECASE (D1).
    options.case_sensitive = False
    # Warstwa absl zapisuje błędy parsowania bezpośrednio na stderr, omijając
    # logging_config.py i zanieczyszczając logi JSON (D5).
    options.log_errors = False
    options.max_mem = RE2_MAX_MEM_BYTES
    return options


def _is_pattern_too_large(e: re2.error) -> bool:
    """Rozpoznaj przekroczenie budżetu pamięci kompilacji (`RE2_MAX_MEM_BYTES`).

    RE2 sygnalizuje to tym samym typem wyjątku co błąd składni (`re2.error`),
    więc trzeba rozróżnić po treści komunikatu, żeby nie sugerować klientowi
    poprawy składni, gdy problemem jest wyłącznie złożoność wzorca (U4).
    """
    if not e.args:
        return False
    detail = e.args[0]
    return isinstance(detail, bytes) and b"pattern too large" in detail


def _re2_error_detail(e: re2.error) -> str:
    """Wydobądź czytelny opis błędu składni z wyjątku `re2.error`.

    Warstwa C++ RE2 zwraca komunikat jako `bytes` (`e.args[0]`), bo wzorzec
    jest kodowany do UTF-8 zanim trafi poza Pythona — interpolowanie tego
    surowo dawałoby polskim znakom repr w stylu `b'...\\xc5\\xbc...'` w
    komunikacie błędu, co myli model językowy będący klientem tego narzędzia
    (U3). Dekodujemy z `errors="replace"`, żeby formatowanie komunikatu błędu
    nigdy samo nie rzuciło wyjątku.
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
    """Zwaliduj i skompiluj wzorzec pochodzący od klienta.

    Args:
        pattern: Surowy wzorzec przekazany przez wywołującego narzędzie.
        max_length: Efektywny limit długości wzorca (po przycięciu do widełek).
        limit_was_clamped: Czy `max_length` powstał z przycięcia konfiguracji.
            Wpływa wyłącznie na treść komunikatu błędu (D3.1).

    Raises:
        PatternValidationError: Wzorzec przekracza limit długości, używa składni
            spoza podzbioru obsługiwanego przez RE2, przekracza budżet pamięci
            kompilacji (`RE2_MAX_MEM_BYTES`), albo zawiera znaki, których nie
            da się zakodować w UTF-8 — np. samotny surogat UTF-16 (U1).
    """
    if len(pattern) > max_length:
        raise PatternValidationError(_too_long_message(len(pattern), max_length, limit_was_clamped))

    try:
        return re2.compile(pattern, _build_options())
    except (re2.error, UnicodeError) as e:
        # `str.encode("utf-8")` wewnątrz `re2/__init__.py` rzuca UnicodeError
        # (np. dla samotnych surogatów) zanim cokolwiek dotknie warstwy C++,
        # więc ten wyjątek nigdy nie jest instancją `re2.error` (U1).
        if isinstance(e, re2.error):
            if _is_pattern_too_large(e):
                raise PatternValidationError(_too_complex_message()) from e
            detail = _re2_error_detail(e)
        else:
            detail = str(e)
        raise PatternValidationError(
            f"Wzorzec nie jest obsługiwany przez silnik wyszukiwania: {detail}. {SUPPORTED_SYNTAX_HINT}"
        ) from e
