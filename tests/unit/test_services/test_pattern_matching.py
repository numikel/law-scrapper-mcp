"""Testy kompilacji wzorców dostarczonych przez klienta (klaster 1 — F01)."""

from __future__ import annotations

from time import perf_counter

import pytest
import re2

from law_scrapper_mcp.services.pattern_matching import (
    SUPPORTED_SYNTAX_HINT,
    PatternValidationError,
    compile_pattern,
)

# Wzorzec z PoC audytu: pod silnikiem `re` nie wraca dla łańcuchów tej długości.
CATASTROPHIC_PATTERN = "(.+)+!"

# Tytuł o realnej długości. Audyt zmierzył na 50 aktach z 2024 r.:
# min 70, śr. 135, maks. 495 znaków.
LONG_TITLE = (
    "Rozporządzenie Ministra Rozwoju i Technologii z dnia 12 kwietnia 2024 r. "
    "zmieniające rozporządzenie w sprawie szczegółowego zakresu i formy projektu "
    "budowlanego oraz warunków technicznych, jakim powinny odpowiadać budynki "
    "i ich usytuowanie, w zakresie wymagań ochrony przeciwpożarowej"
)


def test_long_title_is_representative() -> None:
    """Test PoC ma sens tylko na łańcuchu powyżej progu wykrywalnego zamrożenia."""
    assert len(LONG_TITLE) > 250


@pytest.mark.timeout(5)  # druga warstwa
def test_re2_engine_is_available_and_linear() -> None:
    """Silnik RE2 kompiluje wzorzec z PoC i zwraca brak dopasowania natychmiast."""
    options = re2.Options()
    options.case_sensitive = False
    options.log_errors = False
    options.max_mem = 8 * 1024 * 1024

    compiled = re2.compile(CATASTROPHIC_PATTERN, options)

    # `re` trzyma GIL podczas backtrackingu, więc wątek-timer pytest-timeout
    # (metoda "thread", domyślna na Windows) nigdy nie dostaje sterowania —
    # marker sam w sobie nie jest wiarygodną bramką dla tej klasy awarii.
    # Mierzymy czas jawnie wewnątrz testu jako pierwszą, rozstrzygającą warstwę.
    start = perf_counter()
    assert compiled.search(LONG_TITLE) is None
    assert perf_counter() - start < 0.5

    # Kontrola pozytywna na tych samych options — bez niej test przeszedłby
    # także dla silnika, który nigdy niczego nie dopasowuje.
    assert re2.compile("Ministra", options).search(LONG_TITLE) is not None


# Trzy wzorce udokumentowane w docstringu filter_results.
DOCUMENTED_PATTERNS = [
    "zdrow|Minister Zdrowia|apteka|lekar",
    "budżet.*państw",
    "podatek|VAT|akcyza",
]


class TestCompilePatternSupportedSyntax:
    @pytest.mark.parametrize("pattern", DOCUMENTED_PATTERNS)
    def test_documented_patterns_compile(self, pattern: str) -> None:
        assert compile_pattern(pattern, max_length=512) is not None

    def test_matching_is_case_insensitive(self) -> None:
        compiled = compile_pattern("minister zdrowia", max_length=512)
        assert compiled.search("Rozporządzenie Ministra Zdrowia") is None
        assert compiled.search("Rozporządzenie MINISTER ZDROWIA") is not None

    def test_unicode_property_matches_polish_diacritics(self) -> None:
        compiled = compile_pattern(r"\p{L}+", max_length=512)
        assert compiled.search("żółć") is not None

    def test_posix_class_is_supported(self) -> None:
        compiled = compile_pattern("[[:alpha:]]+", max_length=512)
        assert compiled.search("ustawa") is not None

    @pytest.mark.timeout(5)
    def test_catastrophic_pattern_returns_immediately(self) -> None:
        compiled = compile_pattern(CATASTROPHIC_PATTERN, max_length=512)
        assert compiled.search(LONG_TITLE) is None


class TestCompilePatternRejections:
    @pytest.mark.parametrize(
        "pattern",
        ["(?=foo)bar", "(?<=foo)bar", "(?!foo)bar", r"(a)\1"],
    )
    def test_lookaround_and_backreference_are_rejected(self, pattern: str) -> None:
        with pytest.raises(PatternValidationError) as exc_info:
            compile_pattern(pattern, max_length=512)

        message = str(exc_info.value)
        assert "nie jest obsługiwany" in message
        assert "lookaround" in message.lower()

    def test_pattern_over_limit_is_rejected(self) -> None:
        with pytest.raises(PatternValidationError, match="za długi"):
            compile_pattern("a" * 513, max_length=512)

    def test_length_is_checked_before_compilation(self) -> None:
        """Wzorzec zarazem za długi i składniowo błędny daje błąd DŁUGOŚCI."""
        pattern = "(" * 600  # > 512 znaków i niedomknięte nawiasy

        with pytest.raises(PatternValidationError) as exc_info:
            compile_pattern(pattern, max_length=512)

        assert "za długi" in str(exc_info.value)
        assert "nie jest obsługiwany" not in str(exc_info.value)

    def test_length_error_reports_effective_limit_when_clamped(self) -> None:
        with pytest.raises(PatternValidationError) as exc_info:
            compile_pattern("a" * 5000, max_length=4096, limit_was_clamped=True)

        message = str(exc_info.value)
        assert "4096" in message
        assert "przycięcia" in message

    def test_length_error_omits_clamping_note_when_not_clamped(self) -> None:
        with pytest.raises(PatternValidationError) as exc_info:
            compile_pattern("a" * 600, max_length=512)

        assert "przycięcia" not in str(exc_info.value)

    def test_syntax_error_message_decodes_polish_diacritics(self) -> None:
        """U3: komunikat błędu ma czytelny znak, nie jego reprezentację bajtową.

        `re2.error` niesie komunikat C++ jako `bytes` w `e.args[0]`
        (`re2/__init__.py`: `raise error(self._regexp.error())`). Bez
        dekodowania interpolacja `{e}` dałaby repr w stylu `b'...\\xc5\\xbc-a'`
        zamiast czytelnego `ż-a` — myląc model językowy będący klientem tego
        narzędzia. Zdekodowana treść zweryfikowana empirycznie: RE2 dla
        `"[ż-a]"` cytuje obrażający fragment jako
        `invalid character class range: ż-a`.
        """
        with pytest.raises(PatternValidationError) as exc_info:
            compile_pattern("[ż-a]", max_length=512)

        message = str(exc_info.value)
        assert "ż" in message
        assert "\\xc5\\xbc" not in message
        assert "b'" not in message

    def test_lone_surrogate_is_rejected(self) -> None:
        """U1: kodowanie UTF-8 rzuca UnicodeError zanim RE2 zobaczy wzorzec.

        `re2/__init__.py` koduje wzorzec do UTF-8 przed przekazaniem do
        warstwy C++ — samotny surogat UTF-16 nie da się tak zakodować, więc
        wyjątek powstaje na granicy Python->Python i NIE jest instancją
        `re2.error`. Bez poszerzenia klauzuli `except` ten wyjątek
        przechodziłby przez `compile_pattern` niezłapany.
        """
        with pytest.raises(PatternValidationError) as exc_info:
            compile_pattern("a\ud800b", max_length=512)

        assert "nie jest obsługiwany" in str(exc_info.value)

    def test_pattern_too_large_is_rejected_with_complexity_message(self) -> None:
        """U4: przekroczenie budżetu pamięci to inny problem niż zła składnia.

        `.{1000}` powtórzone 15 razy (105 znaków) jest składniowo poprawne,
        ale przy `RE2_MAX_MEM_BYTES = 2 MiB` przekracza budżet kompilacji —
        wartość dobrana empirycznie TUŻ POWYŻEJ progu (n=14/98 znaków nadal
        się kompiluje, n=15/105 już nie). Kalibracja jest celowo ciasna: pełni
        funkcję strażnika samej stałej `RE2_MAX_MEM_BYTES` — gdyby ktoś
        podniósł ją z powrotem do 8 MiB, ten wzorzec znów by się skompilował
        i test głośno by padł, zamiast milcząco przestać cokolwiek sprawdzać.
        Komunikat musi mówić o złożoności, nie o składni, i nie może zawierać
        `SUPPORTED_SYNTAX_HINT` — inaczej klient dostaje instrukcję usunięcia
        lookaroundów/backreferencji, których wzorzec w ogóle nie zawiera.
        """
        pattern = ".{1000}" * 15

        with pytest.raises(PatternValidationError) as exc_info:
            compile_pattern(pattern, max_length=512)

        message = str(exc_info.value)
        assert "złożon" in message
        assert SUPPORTED_SYNTAX_HINT not in message
        assert "nie jest obsługiwany" not in message


class TestCompilePatternDoesNotPolluteStderr:
    def test_rejected_pattern_writes_nothing_to_stderr(self, capfd) -> None:
        """absl pisze z warstwy C++, więc łapiemy na poziomie deskryptora (capfd)."""
        with pytest.raises(PatternValidationError):
            compile_pattern("(?<=foo)bar", max_length=512)

        captured = capfd.readouterr()
        assert "re2.cc" not in captured.err
        assert captured.err == ""
