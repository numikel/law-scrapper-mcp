"""Tests for compiling client-supplied patterns"""

from __future__ import annotations

import subprocess
import sys
from time import perf_counter

import pytest
import re2

from law_scrapper_mcp.services.pattern_matching import (
    SUPPORTED_SYNTAX_HINT,
    PatternValidationError,
    compile_pattern,
)

CATASTROPHIC_PATTERN = "(.+)+!"
COMPILE_TIMEOUT_SECONDS = 1

LONG_TITLE = (
    "Rozporządzenie Ministra Rozwoju i Technologii z dnia 12 kwietnia 2024 r. "
    "zmieniające rozporządzenie w sprawie szczegółowego zakresu i formy projektu "
    "budowlanego oraz warunków technicznych, jakim powinny odpowiadać budynki "
    "i ich usytuowanie, w zakresie wymagań ochrony przeciwpożarowej"
)


def _compile_in_subprocess(pattern: str) -> subprocess.CompletedProcess[str]:
    """Run compilation in a process that can be terminated on a regression."""
    script = """
import sys
from law_scrapper_mcp.services.pattern_matching import PatternValidationError, compile_pattern

try:
    compile_pattern(sys.stdin.read(), max_length=512)
except PatternValidationError:
    raise SystemExit(0)
raise SystemExit(1)
"""
    try:
        return subprocess.run(
            [sys.executable, "-c", script],
            input=pattern,
            capture_output=True,
            encoding="utf-8",
            timeout=COMPILE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"compile_pattern() did not finish within {COMPILE_TIMEOUT_SECONDS} second in an isolated process.")


def test_long_title_is_representative() -> None:
    """The PoC test is meaningful only on a string above the detectable-freeze threshold."""
    assert len(LONG_TITLE) > 250


@pytest.mark.timeout(5)  # secondary layer
def test_re2_engine_is_available_and_linear() -> None:
    """RE2 compiles the PoC pattern and returns a miss immediately."""
    options = re2.Options()
    options.case_sensitive = False
    options.log_errors = False
    options.max_mem = 8 * 1024 * 1024

    compiled = re2.compile(CATASTROPHIC_PATTERN, options)

    # `re` holds the GIL during backtracking, so the pytest-timeout timer
    # thread (method "thread", default on Windows) never gets scheduled —
    # the marker alone is not a reliable gate for this failure class.
    # Measure time explicitly inside the test as the primary, decisive layer.
    start = perf_counter()
    assert compiled.search(LONG_TITLE) is None
    assert perf_counter() - start < 0.5

    # Positive control on the same options — without it the test would also
    # pass for an engine that never matches anything.
    assert re2.compile("Ministra", options).search(LONG_TITLE) is not None


# Three patterns documented in the filter_results docstring.
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
        """A pattern that is both too long and syntactically invalid yields a LENGTH error."""
        pattern = "(" * 600  # > 512 chars and unclosed parentheses

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

    def test_many_bounded_ranges_are_rejected_before_compilation(self) -> None:
        """Concatenated range quantifiers must not monopolize the event loop."""
        pattern = "a{1,900}" * 62
        start = perf_counter()

        with pytest.raises(PatternValidationError, match="złożony"):
            compile_pattern(pattern, max_length=512)

        assert perf_counter() - start < 0.5

    def test_quoted_literal_bypass_is_rejected_before_compilation(self) -> None:
        """Quoted literals must not hide ranges from the compile-time DoS guard."""
        pattern = r"\Q[\E" + "a{1,900}" * 62

        result = _compile_in_subprocess(pattern)

        assert result.returncode == 0, result.stderr

    def test_four_variable_ranges_remain_supported(self) -> None:
        """The structural guard permits the documented, bounded use case."""
        assert compile_pattern("a{1,2}b{3,5}c{0,3}d{1,4}", max_length=512)

    def test_five_variable_ranges_are_rejected(self) -> None:
        """The first range above the documented limit is rejected."""
        with pytest.raises(PatternValidationError, match="złożony"):
            compile_pattern("a{1,2}b{3,5}c{0,3}d{1,4}e{5,9}", max_length=512)

    @pytest.mark.parametrize(
        "pattern",
        [
            "[[:alpha:]{1,2}]" * 5,
            r"[\{\]]" * 5,
            r"\{1,2\}" * 5,
        ],
    )
    def test_literal_braces_do_not_count_as_variable_ranges(self, pattern: str) -> None:
        """POSIX classes and escaped metacharacters retain their literal meaning."""
        assert compile_pattern(pattern, max_length=512) is not None

    @pytest.mark.parametrize("pattern", [r"\Qliteral\E", r"\Qunterminated", r"\E"])
    def test_quoted_literal_constructs_are_rejected(self, pattern: str) -> None:
        """The supported subset excludes quoted literals before RE2 compilation."""
        with pytest.raises(PatternValidationError, match="Cytowane literały"):
            compile_pattern(pattern, max_length=512)

    def test_many_unmatched_braces_are_scanned_in_linear_time(self) -> None:
        """Malformed braces must not cause repeated suffix scans in the preflight."""
        start = perf_counter()

        compile_pattern("{" * 4096, max_length=4096)

        assert perf_counter() - start < 0.5

    def test_syntax_error_message_decodes_polish_diacritics(self) -> None:
        """The error message must show a readable character, not its byte repr.

        `re2.error` carries the C++ message as `bytes` in `e.args[0]`
        (`re2/__init__.py`: `raise error(self._regexp.error())`). Without
        decoding, interpolating `{e}` would produce a repr like
        `b'...\\xc5\\xbc-a'` instead of readable `ż-a` — confusing the
        language-model client of this tool. Empirically verified decoded
        content: for `"[ż-a]"`, RE2 quotes the offending fragment as
        `invalid character class range: ż-a`.
        """
        with pytest.raises(PatternValidationError) as exc_info:
            compile_pattern("[ż-a]", max_length=512)

        message = str(exc_info.value)
        assert "ż" in message
        assert "\\xc5\\xbc" not in message
        assert "b'" not in message

    def test_lone_surrogate_is_rejected(self) -> None:
        """UTF-8 encoding raises UnicodeError before RE2 sees the pattern.

        `re2/__init__.py` encodes the pattern to UTF-8 before handing it to the
        C++ layer — a lone UTF-16 surrogate cannot be so encoded, so the
        exception arises on the Python->Python boundary and is NOT an instance
        of `re2.error`. Without widening the `except` clause this exception
        would propagate through `compile_pattern` uncaught.
        """
        with pytest.raises(PatternValidationError) as exc_info:
            compile_pattern("a\ud800b", max_length=512)

        assert "nie jest obsługiwany" in str(exc_info.value)

    def test_pattern_too_large_is_rejected_with_complexity_message(self) -> None:
        """U4: exceeding the memory budget is a different problem from bad syntax.

        `.{1000}` repeated 15 times (105 chars) is syntactically valid but at
        `RE2_MAX_MEM_BYTES = 2 MiB` exceeds the compilation budget — a value
        chosen empirically JUST ABOVE the threshold (n=14/98 chars still
        compiles, n=15/105 does not). The calibration is intentionally tight:
        it guards the `RE2_MAX_MEM_BYTES` constant itself — if someone raised
        it back to 8 MiB, this pattern would compile again and the test would
        fail loudly instead of silently stopping to check anything.
        The message must talk about complexity, not syntax, and must not
        include `SUPPORTED_SYNTAX_HINT` — otherwise the client gets instructions
        to remove lookarounds/backreferences the pattern does not contain.
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
        """absl writes from the C++ layer, so we catch at the descriptor level (capfd)."""
        with pytest.raises(PatternValidationError):
            compile_pattern("(?<=foo)bar", max_length=512)

        captured = capfd.readouterr()
        assert "re2.cc" not in captured.err
        assert captured.err == ""
