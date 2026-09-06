"""Tests for the shared MCP tool error classification."""

from __future__ import annotations

import logging

import httpx
import pytest

from law_scrapper_mcp.client.exceptions import (
    ActNotFoundError,
    ApiUnavailableError,
    ContentTooLargeError,
    SejmApiError,
)
from law_scrapper_mcp.services.result_store import ResultSetNotFoundError, ResultSetTooLargeError
from law_scrapper_mcp.tools.error_handling import ToolExecutionError, _classify_error, handle_tool_errors

# Bound to the value under test so the assertion does not restate the wording.
_CATEGORY_GUIDANCE_SENTENCE_FOR_VALIDATION = "Popraw parametr wywołania i spróbuj ponownie."


class TestCategoryGuidance:
    """Each category tells the caller what to do next (A12, D4).

    The category name itself stays out of the message: naming it would freeze
    an undocumented protocol in a text field and leak implementation detail.
    """

    @pytest.mark.parametrize(
        ("exc", "category"),
        [
            (ActNotFoundError("DU/2024/1"), "not_found"),
            (ValueError("zły parametr"), "validation"),
            (ResultSetNotFoundError("rs_1"), "precondition"),
            (
                ContentTooLargeError(
                    eli="DU/2024/1", size_bytes=9_000_000, limit_bytes=5_242_880, pdf_url="https://example/text.pdf"
                ),
                "content_too_large",
            ),
            (ApiUnavailableError("down", status_code=503), "unavailable"),
            (SejmApiError("HTTP 500: body", status_code=500), "upstream"),
            (KeyError("boom"), "internal"),
        ],
    )
    def test_every_category_ends_with_its_guidance(self, exc: Exception, category: str) -> None:
        from law_scrapper_mcp.tools.error_handling import _CATEGORY_GUIDANCE, _public_message

        message = _public_message(exc, _classify_error(exc))

        assert message.endswith(_CATEGORY_GUIDANCE[category])
        assert category not in message

    def test_guidance_covers_every_category_the_classifier_can_return(self) -> None:
        from law_scrapper_mcp.tools.error_handling import _CATEGORY_GUIDANCE, _ERROR_CATEGORIES

        assert set(_ERROR_CATEGORIES.values()) | {"internal"} == set(_CATEGORY_GUIDANCE)

    def test_transient_and_permanent_guidance_differ(self) -> None:
        """The whole point of D4: 'retry' must not read like 'does not exist'."""
        from law_scrapper_mcp.tools.error_handling import _CATEGORY_GUIDANCE

        assert _CATEGORY_GUIDANCE["unavailable"] != _CATEGORY_GUIDANCE["not_found"]

    def test_content_too_large_has_its_own_guidance_distinct_from_precondition(self) -> None:
        """`ContentTooLargeError` has no prior step to perform — reusing
        `precondition`'s "do a step first" wording would misdirect the model,
        since the actionable remedy (fetch the source file) is already the
        last sentence of the body, not something a retry could satisfy."""
        from law_scrapper_mcp.tools.error_handling import _CATEGORY_GUIDANCE

        assert _CATEGORY_GUIDANCE["content_too_large"] != _CATEGORY_GUIDANCE["precondition"]
        assert "krok" not in _CATEGORY_GUIDANCE["content_too_large"]


class TestClassifyError:
    """ResultSetTooLargeError must classify as precondition"""

    def test_result_set_not_found_is_precondition(self) -> None:
        assert _classify_error(ResultSetNotFoundError("rs_1")) == "precondition"

    def test_result_set_too_large_is_precondition(self) -> None:
        assert _classify_error(ResultSetTooLargeError("rs_1", 500, 100)) == "precondition"

    def test_upstream_status_is_not_an_internal_error(self) -> None:
        error = SejmApiError("HTTP 429: too many requests", status_code=429, url="https://api.sejm.gov.pl/eli/acts")
        assert _classify_error(error) == "upstream"

    def test_upstream_timeout_is_not_an_internal_error(self) -> None:
        assert _classify_error(httpx.TimeoutException("timed out")) == "upstream"

    def test_narrow_subclasses_still_win_over_sejm_api_error(self) -> None:
        """`ActNotFoundError` and `ApiUnavailableError` subclass `SejmApiError`."""
        assert _classify_error(ActNotFoundError("DU/2024/1")) == "not_found"
        assert _classify_error(ApiUnavailableError("down", status_code=503, url="u")) == "unavailable"


@pytest.mark.asyncio
class TestHandleToolErrorsPublicSurface:
    """Exercise the public `handle_tool_errors` surface, not `_classify_error`."""

    async def test_upstream_failure_hides_the_response_body(self, caplog: pytest.LogCaptureFixture) -> None:
        """The upstream body must stay out of both the response and the log.

        A 4xx body can echo back the parameters of the rejected request, so
        keeping it off the tool response while still writing it to stderr
        would leave the same exposure open on the durable side. The status
        survives on ERROR — it cannot carry caller input.
        """
        secret_body = "<html>internal upstream trace</html>"

        @handle_tool_errors
        async def failing_tool() -> str:
            raise SejmApiError(f"HTTP 500: {secret_body}", status_code=500, url="https://api.sejm.gov.pl/eli/acts")

        with caplog.at_level(logging.DEBUG, logger="law_scrapper_mcp.tools.error_handling"):
            with pytest.raises(ToolExecutionError) as exc_info:
                await failing_tool()

        message = str(exc_info.value)
        assert "api.sejm.gov.pl nie odpowiedział poprawnie" in message
        assert secret_body not in message

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]

        assert error_records
        assert all(secret_body not in r.getMessage() for r in error_records)
        assert any("HTTP 500" in r.getMessage() for r in error_records)
        assert not any(r.exc_info for r in error_records)
        assert any(secret_body in r.getMessage() for r in debug_records)

    async def test_upstream_timeout_without_status_logs_no_http_suffix(self, caplog: pytest.LogCaptureFixture) -> None:
        """`httpx.TimeoutException` is `upstream` but carries no status code."""

        @handle_tool_errors
        async def failing_tool() -> str:
            raise httpx.TimeoutException("read timed out")

        with caplog.at_level(logging.ERROR, logger="law_scrapper_mcp.tools.error_handling"):
            with pytest.raises(ToolExecutionError):
                await failing_tool()

        assert caplog.records
        assert caplog.records[-1].getMessage() == "Tool failing_tool failed [upstream]"

    async def test_result_set_too_large_is_precondition_without_traceback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        @handle_tool_errors
        async def failing_tool() -> str:
            raise ResultSetTooLargeError("rs_1", 500, 100)

        with caplog.at_level(logging.ERROR, logger="law_scrapper_mcp.tools.error_handling"):
            with pytest.raises(ToolExecutionError, match="rs_1"):
                await failing_tool()

        assert caplog.records
        assert not caplog.records[-1].exc_info

    async def test_validation_failure_keeps_detail_off_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """A validation message is written by the caller and can carry a regex
        pattern or an act title. The public exception still returns it — F13
        is about the durable stderr record, not about the tool response."""
        detail = "wzorzec 'zdrowie|przymusowe' jest nieprawidłowy"

        @handle_tool_errors
        async def failing_tool() -> str:
            raise ValueError(detail)

        with caplog.at_level(logging.DEBUG, logger="law_scrapper_mcp.tools.error_handling"):
            with pytest.raises(ToolExecutionError, match="nieprawidłowy"):
                await failing_tool()

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]

        assert error_records
        assert all(detail not in r.getMessage() for r in error_records)
        assert any("failing_tool" in r.getMessage() for r in error_records)
        assert any(detail in r.getMessage() for r in debug_records)


@pytest.mark.asyncio
async def test_content_too_large_message_survives_sanitization() -> None:
    """The agent must learn that the act exists and where to fetch it.

    Unregistered exceptions fall into the `internal` category, whose public
    message is a fixed generic string — that would swallow both the Polish
    wording and the source URL.
    """
    from law_scrapper_mcp.client.exceptions import ContentTooLargeError
    from law_scrapper_mcp.tools.error_handling import _CATEGORY_GUIDANCE, ToolExecutionError, handle_tool_errors

    @handle_tool_errors
    async def failing_tool() -> None:
        raise ContentTooLargeError(
            eli="DU/2024/1",
            size_bytes=9_000_000,
            limit_bytes=5_242_880,
            pdf_url="https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.pdf",
        )

    with pytest.raises(ToolExecutionError) as excinfo:
        await failing_tool()

    message = str(excinfo.value)
    # A14, as amended by D4/D9: the URL survives in full and nothing is truncated.
    # It is no longer the last text in the message, because every category now
    # carries a remediation sentence (A12) — but `ContentTooLargeError` gets its
    # own `content_too_large` category rather than sharing `precondition`'s
    # "do a prior step" wording, which does not describe this situation (there
    # is no prior step; the file is simply too large).
    assert "DU/2024/1" in message
    assert "https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.pdf" in message
    assert "przekracza limit" in message
    assert "wewnętrzny błąd" not in message
    assert "…" not in message
    assert message.endswith(_CATEGORY_GUIDANCE["content_too_large"])


def test_content_too_large_is_classified_as_content_too_large() -> None:
    from law_scrapper_mcp.client.exceptions import ContentTooLargeError
    from law_scrapper_mcp.tools.error_handling import _classify_error

    error = ContentTooLargeError(
        eli="DU/2024/1",
        size_bytes=9_000_000,
        limit_bytes=5_242_880,
        pdf_url="https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.pdf",
    )

    assert _classify_error(error) == "content_too_large"


class TestMessageTruncation:
    """`str(exc)` messages are bounded; project-authored ones are not (A13, A16, D8)."""

    def test_long_validation_message_is_truncated(self) -> None:
        from law_scrapper_mcp.config import settings
        from law_scrapper_mcp.tools.error_handling import _TRUNCATION_SUFFIX, _public_message

        limit = settings.error_message_max_chars
        exc = ValueError("A" * (limit * 3))

        message = _public_message(exc, "validation")
        body = message.removesuffix(_CATEGORY_GUIDANCE_SENTENCE_FOR_VALIDATION).rstrip()

        assert len(body) <= limit
        assert body.endswith(_TRUNCATION_SUFFIX)

    def test_a_short_message_is_left_alone(self) -> None:
        from law_scrapper_mcp.tools.error_handling import _TRUNCATION_SUFFIX, _public_message

        message = _public_message(ValueError("krótki komunikat"), "validation")

        assert "krótki komunikat" in message
        assert _TRUNCATION_SUFFIX not in message

    def test_upstream_body_never_reaches_the_message(self) -> None:
        """A 40 kB upstream body is excluded by category, not by length (A16, F39)."""
        from law_scrapper_mcp.tools.error_handling import _public_message

        body = "<html>" + ("x" * 40_000) + "</html>"
        exc = SejmApiError(f"HTTP 500: {body}", status_code=500, url="https://api.sejm.gov.pl/eli/acts")

        message = _public_message(exc, "upstream")

        assert "x" * 100 not in message
        assert len(message) < 300

    def test_the_limit_is_configurable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from law_scrapper_mcp import config
        from law_scrapper_mcp.tools.error_handling import _public_message

        monkeypatch.setattr(config.settings, "error_message_max_chars", 80)
        message = _public_message(ValueError("B" * 500), "validation")

        assert len(message) < 500


class TestTruncationKeepsTrailingUrl:
    """A trailing URL is the one token the caller can act on, so the cut lands
    in the prefix instead (#60). The cap still wins when even the URL and the
    announcement together would breach it — a bound that bends for a long URL
    is not a bound."""

    _PDF_URL = "https://api.sejm.gov.pl/eli/acts/DU/2024/1716/text.pdf"

    def _oversized_act(self) -> ContentTooLargeError:
        return ContentTooLargeError(
            eli="DU/2024/1716", size_bytes=9_000_000, limit_bytes=5_242_880, pdf_url=self._PDF_URL
        )

    def _body(self, message: str) -> str:
        from law_scrapper_mcp.tools.error_handling import _CATEGORY_GUIDANCE

        return message.removesuffix(_CATEGORY_GUIDANCE["content_too_large"]).rstrip()

    def test_a_trailing_url_survives_when_the_prefix_can_be_cut_instead(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from law_scrapper_mcp import config
        from law_scrapper_mcp.tools.error_handling import _TRUNCATION_SUFFIX, _public_message

        monkeypatch.setattr(config.settings, "error_message_max_chars", 120)
        body = self._body(_public_message(self._oversized_act(), "content_too_large"))

        assert len(body) <= 120
        assert body.endswith(self._PDF_URL)
        assert _TRUNCATION_SUFFIX in body
        assert body.startswith("Treść aktu DU/2024/1716")

    def test_a_url_that_does_not_fit_beside_the_suffix_falls_back_to_the_plain_cut(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deliberate trade-off, not an accident: at the configured floor of 80
        the URL (54 chars) plus the announcement (26 chars) already exceeds the
        cap, so nothing of the prefix could remain. The bound is honoured and
        the URL is lost."""
        from law_scrapper_mcp import config
        from law_scrapper_mcp.tools.error_handling import _TRUNCATION_SUFFIX, _public_message

        monkeypatch.setattr(config.settings, "error_message_max_chars", 80)
        body = self._body(_public_message(self._oversized_act(), "content_too_large"))

        assert len(body) <= 80
        assert body.endswith(_TRUNCATION_SUFFIX)
        assert self._PDF_URL not in body

    def test_a_message_ending_in_an_overlong_url_honours_the_bound(self) -> None:
        """A caller-sourced message can end in a URL of any length; keeping it
        whole would hand the caller a way past the cap."""
        from law_scrapper_mcp.config import settings
        from law_scrapper_mcp.tools.error_handling import _TRUNCATION_SUFFIX, _public_message

        limit = settings.error_message_max_chars
        exc = ValueError("adres https://example.invalid/" + "a" * (limit + 100))

        message = _public_message(exc, "validation")
        body = message.removesuffix(_CATEGORY_GUIDANCE_SENTENCE_FOR_VALIDATION).rstrip()

        assert len(body) <= limit
        assert body.endswith(_TRUNCATION_SUFFIX)

    def test_a_url_ending_message_that_fits_is_untouched(self) -> None:
        from law_scrapper_mcp.tools.error_handling import _TRUNCATION_SUFFIX, _public_message

        message = _public_message(self._oversized_act(), "content_too_large")

        assert _TRUNCATION_SUFFIX not in message
        assert f"{self._PDF_URL} " in message


class TestMessagePunctuation:
    """The body's clause is terminated before the guidance sentence starts (D4)."""

    def test_a_body_without_terminal_punctuation_gets_one(self) -> None:
        from law_scrapper_mcp.tools.error_handling import _public_message

        message = _public_message(ValueError("brak kropki"), "validation")

        assert "brak kropki. Popraw parametr" in message

    def test_a_body_that_already_ends_in_a_terminator_is_not_doubled(self) -> None:
        from law_scrapper_mcp.tools.error_handling import _UPSTREAM_MESSAGE, _public_message

        exc = SejmApiError("HTTP 500: body", status_code=500, url="https://api.sejm.gov.pl/eli/acts")
        message = _public_message(exc, "upstream")

        assert ".." not in message
        assert message.startswith(_UPSTREAM_MESSAGE)

    @pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
    def test_a_blank_body_leaves_no_leading_space_and_does_not_crash(self, blank: str) -> None:
        """`"   ".rsplit(maxsplit=1)` used to return `[]`, crashing `[-1]` with
        an `IndexError` that escaped `handle_tool_errors` unsanitized."""
        from law_scrapper_mcp.tools.error_handling import _CATEGORY_GUIDANCE, _public_message

        message = _public_message(ValueError(blank), "validation")

        assert message == _CATEGORY_GUIDANCE["validation"]

    def test_a_body_ending_in_a_url_is_not_glued_to_a_period(self) -> None:
        """A period fused onto a URL is a copy-paste/auto-link hazard."""
        from law_scrapper_mcp.client.exceptions import ContentTooLargeError
        from law_scrapper_mcp.tools.error_handling import _public_message

        pdf_url = "https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.pdf"
        exc = ContentTooLargeError(
            eli="DU/2024/1",
            size_bytes=9_000_000,
            limit_bytes=5_242_880,
            pdf_url=pdf_url,
        )

        message = _public_message(exc, "content_too_large")

        assert f"{pdf_url} " in message
        assert f"{pdf_url}." not in message


class TestTerminated:
    """`_terminated` in isolation, independent of category routing."""

    def test_adds_a_period_when_missing(self) -> None:
        from law_scrapper_mcp.tools.error_handling import _terminated

        assert _terminated("brak kropki") == "brak kropki."

    @pytest.mark.parametrize("terminator", [".", "!", "?", "…"])
    def test_does_not_double_an_existing_terminator(self, terminator: str) -> None:
        from law_scrapper_mcp.tools.error_handling import _terminated

        text = f"już zakończone{terminator}"
        assert _terminated(text) == text

    def test_does_not_glue_a_period_onto_a_url(self) -> None:
        from law_scrapper_mcp.tools.error_handling import _terminated

        text = "Pobierz plik źródłowy: https://api.sejm.gov.pl/eli/acts/DU/2024/1/text.pdf"
        assert _terminated(text) == text

    @pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
    def test_whitespace_only_text_passes_through_unchanged(self, blank: str) -> None:
        from law_scrapper_mcp.tools.error_handling import _terminated

        assert _terminated(blank) == blank


class TestCallerSourcedCategories:
    """D8's truncation boundary is source-shaped, not an implicit `else`."""

    def test_caller_sourced_categories_cover_every_category_except_internal_and_upstream(self) -> None:
        from law_scrapper_mcp.tools.error_handling import _CALLER_SOURCED_CATEGORIES, _ERROR_CATEGORIES

        all_categories = set(_ERROR_CATEGORIES.values()) | {"internal"}
        assert _CALLER_SOURCED_CATEGORIES | {"internal", "upstream"} == all_categories
