"""Content processing for legal acts - HTML/PDF to Markdown conversion."""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Section:
    """Represents a section in a legal document."""

    id: str
    title: str
    level: int  # heading level (1=chapter, 2=article, 3=paragraph)
    start_pos: int
    end_pos: int | None = None
    content: str = ""


_CID_MARKER = re.compile(r"\(cid:\d+\)")

# A text layer whose glyphs have no Unicode mapping extracts as `(cid:N)` markers. Both bounds
# must hold, so a handful of stray markers in a long clean text does not raise the flag.
# Hypothesis taken from the recorded journal PDFs, not a measured threshold.
_BROKEN_LAYER_MIN_MARKERS = 10
_BROKEN_LAYER_MIN_SHARE = 0.005


@dataclass(frozen=True)
class PdfInspection:
    """What a PDF turned out to contain.

    Attributes:
        text: The extracted text, pages joined by a blank line; empty for a scan or a failure.
        pages: Number of pages seen before the extraction ended.
        image_only_pages: Pages with no characters and at least one image (a scanned page).
        failed: The file could not be read at all.
        error: Description of the failure when `failed`.
        cid_markers: Number of `(cid:N)` markers in `text`.
        broken_layer: The text layer is dominated by `(cid:N)` markers, so the text is garbled.
    """

    text: str = ""
    pages: int = 0
    image_only_pages: int = 0
    failed: bool = False
    error: str | None = None
    cid_markers: int = 0
    broken_layer: bool = False


class ContentProcessor:
    """Processes legal act content from HTML/PDF to structured Markdown."""

    def html_to_markdown(self, html: str) -> str:
        """Convert HTML to Markdown using markdownify."""
        from markdownify import markdownify

        md = markdownify(html, heading_style="ATX", strip=["img", "script", "style"])
        # Clean up: remove excessive blank lines, normalize whitespace
        md = re.sub(r"\n{3,}", "\n\n", md)
        return md.strip()

    def pdf_to_text(self, pdf_bytes: bytes) -> str:
        """Extract text from PDF using pdfplumber."""
        try:
            import io

            import pdfplumber

            text_parts = []
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
            if not text_parts:
                return ""
            return "\n\n".join(text_parts)
        except Exception as e:
            logger.warning(f"PDF extraction failed: {e}")
            return ""

    def inspect_pdf(self, pdf_bytes: bytes) -> PdfInspection:
        """Extract text from a PDF and say what kind of file it was.

        Unlike `pdf_to_text`, which collapses every problem into an empty string, this tells a
        scan (pages with images and no characters) from a file that could not be read, and
        flags a text layer that extracts as `(cid:N)` markers. It never raises.
        """
        import io

        import pdfplumber

        parts: list[str] = []
        pages = 0
        image_only = 0
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page in pdf.pages:
                    pages += 1
                    if not page.chars and page.images:
                        image_only += 1
                    page_text = page.extract_text()
                    if page_text:
                        parts.append(page_text)
        except Exception as error:
            return PdfInspection(
                pages=pages,
                image_only_pages=image_only,
                failed=True,
                error=f"{type(error).__name__}: {error}",
            )
        text = "\n\n".join(parts)
        found = _CID_MARKER.findall(text)
        markers = len(found)
        marker_chars = sum(len(marker) for marker in found)
        broken = markers >= _BROKEN_LAYER_MIN_MARKERS and marker_chars >= _BROKEN_LAYER_MIN_SHARE * len(text)
        return PdfInspection(
            text=text,
            pages=pages,
            image_only_pages=image_only,
            cid_markers=markers,
            broken_layer=broken,
        )

    def index_sections(self, markdown: str) -> list[Section]:
        """Parse markdown and create section index based on headings."""
        sections = []
        # Match markdown headings and also Polish legal patterns like "Art. 1.", "Rozdział 1"
        heading_pattern = re.compile(
            r"^(#{1,6})\s+(.+)$|^(Art\.\s*\d+[a-z]?\.?)(.*)$|^(Rozdział\s+\w+)(.*)$|^(DZIAŁ\s+\w+)(.*)$",
            re.MULTILINE,
        )

        matches = list(heading_pattern.finditer(markdown))
        for i, match in enumerate(matches):
            if match.group(1):  # Markdown heading
                level = len(match.group(1))
                title = match.group(2).strip()
            elif match.group(3):  # Art. pattern
                level = 2
                title = (match.group(3) + (match.group(4) or "")).strip()
            elif match.group(5):  # Rozdział pattern
                level = 1
                title = (match.group(5) + (match.group(6) or "")).strip()
            elif match.group(7):  # DZIAŁ pattern
                level = 1
                title = (match.group(7) + (match.group(8) or "")).strip()
            else:
                continue

            start_pos = match.start()
            end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)

            section_id = re.sub(r"[^\w\s.-]", "", title).strip().replace(" ", "_")[:50]

            sections.append(
                Section(
                    id=section_id,
                    title=title,
                    level=level,
                    start_pos=start_pos,
                    end_pos=end_pos,
                    content=markdown[start_pos:end_pos].strip(),
                )
            )

        return sections
