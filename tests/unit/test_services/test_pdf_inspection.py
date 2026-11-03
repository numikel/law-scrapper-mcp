"""`ContentProcessor.inspect_pdf`: a scan, a failure, a good text layer and a garbled one."""

from __future__ import annotations

from pathlib import Path

import pytest

from law_scrapper_mcp.services.content_processor import ContentProcessor

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "edziennik"


class _Page:
    def __init__(self, text: str, *, chars: int | None = None, images: int = 0) -> None:
        self._text = text
        self.chars = [object()] * (len(text) if chars is None else chars)
        self.images = [object()] * images

    def extract_text(self) -> str:
        return self._text


class _Pdf:
    def __init__(self, pages: list[_Page]) -> None:
        self.pages = pages

    def __enter__(self) -> _Pdf:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _stand_in(monkeypatch: pytest.MonkeyPatch, pages: list[_Page]) -> None:
    monkeypatch.setattr("pdfplumber.open", lambda _stream: _Pdf(pages))


def test_a_recorded_scan_has_pages_but_no_text() -> None:
    result = ContentProcessor().inspect_pdf((FIXTURES / "scan_no_text.pdf").read_bytes())

    assert result.pages >= 1
    assert result.image_only_pages == result.pages
    assert result.text == ""
    assert not result.failed
    assert not result.broken_layer


def test_a_recorded_text_layer_gives_text() -> None:
    result = ContentProcessor().inspect_pdf((FIXTURES / "text_layer.pdf").read_bytes())

    assert result.pages == 2
    assert result.image_only_pages == 0
    assert len(result.text) > 1000
    assert not result.failed
    assert not result.broken_layer
    assert result.cid_markers == 0


def test_corrupt_bytes_fail_without_raising() -> None:
    result = ContentProcessor().inspect_pdf(b"definitely not a pdf")

    assert result.failed is True
    assert result.error
    assert result.text == ""


def test_the_failure_keeps_the_page_counts_seen_so_far(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Exploding(_Page):
        def extract_text(self) -> str:
            raise RuntimeError("boom")

    _stand_in(monkeypatch, [_Page("Art. 1. ok"), _Exploding("x")])

    result = ContentProcessor().inspect_pdf(b"stand-in")

    assert result.failed is True
    assert result.pages == 2
    assert "RuntimeError: boom" in (result.error or "")


def test_a_page_with_images_and_no_characters_counts_as_image_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _stand_in(monkeypatch, [_Page("", chars=0, images=1), _Page("Art. 1. tekst"), _Page("", chars=0, images=0)])

    result = ContentProcessor().inspect_pdf(b"stand-in")

    assert (result.pages, result.image_only_pages) == (3, 1), "a blank page without an image is not a scan"
    assert result.text == "Art. 1. tekst"


def test_pages_are_joined_by_a_blank_line(monkeypatch: pytest.MonkeyPatch) -> None:
    _stand_in(monkeypatch, [_Page("pierwsza"), _Page("druga")])

    assert ContentProcessor().inspect_pdf(b"stand-in").text == "pierwsza\n\ndruga"


def test_a_layer_dominated_by_cid_markers_is_flagged_but_keeps_its_text(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Art. 1. " + "(cid:5)" * 30 + " koniec"
    _stand_in(monkeypatch, [_Page(text)])

    result = ContentProcessor().inspect_pdf(b"stand-in")

    assert result.broken_layer is True
    assert result.cid_markers == 30
    assert result.text == text, "flagged, never discarded"


def test_a_few_stray_markers_in_a_long_clean_text_are_not_a_broken_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    clean = "Uchwała wchodzi w życie z dniem podjęcia. " * 400
    _stand_in(monkeypatch, [_Page(clean + "(cid:3)" * 12)])

    result = ContentProcessor().inspect_pdf(b"stand-in")

    assert result.cid_markers == 12
    assert result.broken_layer is False, "12 markers in about 17 000 characters is under half a percent"


def test_fewer_than_ten_markers_never_flag_the_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    _stand_in(monkeypatch, [_Page("(cid:1)" * 9)])

    assert ContentProcessor().inspect_pdf(b"stand-in").broken_layer is False
