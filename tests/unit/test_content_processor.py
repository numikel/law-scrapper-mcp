"""Tests for ContentProcessor service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from law_scrapper_mcp.services.content_processor import ContentProcessor, Section


class TestHtmlToMarkdown:
    """Tests for HTML to Markdown conversion."""

    def test_html_to_markdown_basic(self, content_processor: ContentProcessor):
        """Test basic HTML to Markdown conversion."""
        html = "<h1>Title</h1><p>Paragraph text.</p>"
        md = content_processor.html_to_markdown(html)
        assert "# Title" in md
        assert "Paragraph text." in md

    def test_html_to_markdown_with_sample(
        self, content_processor: ContentProcessor, sample_act_html: str
    ):
        """Test HTML to Markdown with sample act HTML."""
        md = content_processor.html_to_markdown(sample_act_html)
        assert "USTAWA z dnia 1 stycznia 2024 r." in md
        assert "Rozdział 1" in md
        assert "Art. 1." in md
        assert "Art. 2." in md
        assert "Art. 3." in md

    def test_html_to_markdown_strips_scripts(self, content_processor: ContentProcessor):
        """Test that script tags are stripped from output."""
        html = "<h1>Title</h1><script>alert('test');</script><p>Text</p>"
        md = content_processor.html_to_markdown(html)
        # markdownify strips <script> tags but may leave text content
        assert "# Title" in md
        assert "Text" in md
        assert "<script>" not in md

    def test_html_to_markdown_normalizes_whitespace(
        self, content_processor: ContentProcessor
    ):
        """Test that excessive blank lines are removed."""
        html = "<h1>Title</h1>\n\n\n\n<p>Text</p>"
        md = content_processor.html_to_markdown(html)
        # Should not have more than 2 consecutive newlines
        assert "\n\n\n" not in md

    def test_html_to_markdown_empty_input(self, content_processor: ContentProcessor):
        """Test HTML to Markdown with empty input."""
        md = content_processor.html_to_markdown("")
        assert md == ""

    def test_html_to_markdown_with_lists(self, content_processor: ContentProcessor):
        """Test HTML to Markdown with ordered lists."""
        html = "<ol><li>First item</li><li>Second item</li></ol>"
        md = content_processor.html_to_markdown(html)
        assert "First item" in md
        assert "Second item" in md


class TestPdfToText:
    """Tests for PDF to text extraction."""

    def test_pdf_to_text_empty_input(self, content_processor: ContentProcessor):
        """Test PDF to text with empty input."""
        text = content_processor.pdf_to_text(b"")
        assert text == ""

    @patch("pdfplumber.open")
    def test_pdf_to_text_with_mock(self, mock_pdfplumber, content_processor: ContentProcessor):
        """Test PDF to text extraction with mocked pdfplumber."""
        # Create mock PDF with pages
        mock_page1 = MagicMock()
        mock_page1.extract_text.return_value = "Page 1 text"

        mock_page2 = MagicMock()
        mock_page2.extract_text.return_value = "Page 2 text"

        mock_pdf = MagicMock()
        mock_pdf.__enter__.return_value.pages = [mock_page1, mock_page2]

        mock_pdfplumber.return_value = mock_pdf

        text = content_processor.pdf_to_text(b"fake pdf bytes")

        assert "Page 1 text" in text
        assert "Page 2 text" in text
        assert "\n\n" in text  # Pages should be separated

    @patch("pdfplumber.open")
    def test_pdf_to_text_extraction_failure(
        self, mock_pdfplumber, content_processor: ContentProcessor
    ):
        """Test that PDF extraction failures are handled gracefully."""
        mock_pdfplumber.side_effect = Exception("PDF parsing error")

        text = content_processor.pdf_to_text(b"invalid pdf")

        # Should return empty string on error, not raise
        assert text == ""

    @patch("pdfplumber.open")
    def test_pdf_to_text_no_text_on_page(
        self, mock_pdfplumber, content_processor: ContentProcessor
    ):
        """Test PDF with pages that have no text."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = None

        mock_pdf = MagicMock()
        mock_pdf.__enter__.return_value.pages = [mock_page]

        mock_pdfplumber.return_value = mock_pdf

        text = content_processor.pdf_to_text(b"fake pdf")
        assert text == ""


class TestIndexSections:
    """Tests for section indexing."""

    def test_index_sections_with_markdown_headings(
        self, content_processor: ContentProcessor
    ):
        """Test indexing sections with standard Markdown headings."""
        markdown = """# Chapter 1
Content of chapter 1

## Section 1.1
Content of section 1.1

## Section 1.2
Content of section 1.2"""

        sections = content_processor.index_sections(markdown)

        assert len(sections) >= 2
        assert any(s.title == "Chapter 1" for s in sections)
        assert any("Section 1.1" in s.title for s in sections)

    def test_index_sections_with_art_pattern(self, content_processor: ContentProcessor):
        """Test indexing sections with Art. pattern."""
        markdown = """Art. 1. First article content.

Art. 2. Second article content.

Art. 3. Third article content."""

        sections = content_processor.index_sections(markdown)

        assert len(sections) >= 3
        art_sections = [s for s in sections if s.title.startswith("Art.")]
        assert len(art_sections) >= 3

    def test_index_sections_with_rozdzial_pattern(
        self, content_processor: ContentProcessor
    ):
        """Test indexing sections with Rozdział pattern."""
        markdown = """Rozdział 1 General provisions

Some content here.

Rozdział 2 Special provisions

More content here."""

        sections = content_processor.index_sections(markdown)

        rozdzial_sections = [s for s in sections if "Rozdział" in s.title]
        assert len(rozdzial_sections) >= 2

    def test_index_sections_with_sample_act(
        self, content_processor: ContentProcessor, sample_act_html: str
    ):
        """Test indexing sections with sample act HTML."""
        markdown = content_processor.html_to_markdown(sample_act_html)
        sections = content_processor.index_sections(markdown)

        assert len(sections) > 0

        # Should find Art. sections
        art_sections = [s for s in sections if "Art." in s.title]
        assert len(art_sections) >= 3

        # Check section properties
        for section in sections:
            assert section.id
            assert section.title
            assert section.level > 0
            assert section.start_pos >= 0
            assert section.end_pos is None or section.end_pos > section.start_pos

    def test_index_sections_empty_markdown(self, content_processor: ContentProcessor):
        """Test indexing with empty markdown."""
        sections = content_processor.index_sections("")
        assert sections == []

    def test_index_sections_no_headings(self, content_processor: ContentProcessor):
        """Test indexing markdown with no headings."""
        markdown = "Just plain text without any headings."
        sections = content_processor.index_sections(markdown)
        assert sections == []

    def test_section_content_extraction(self, content_processor: ContentProcessor):
        """Test that section content is properly extracted."""
        markdown = """# Section 1
Content for section 1.

# Section 2
Content for section 2."""

        sections = content_processor.index_sections(markdown)

        assert len(sections) >= 2
        assert "Content for section 1" in sections[0].content
        if len(sections) > 1:
            assert "Content for section 2" in sections[1].content

    def test_section_id_generation(self, content_processor: ContentProcessor):
        """Test that section IDs are properly generated."""
        markdown = """# Test Section With Spaces

Content here.

Art. 123. Article with number."""

        sections = content_processor.index_sections(markdown)

        for section in sections:
            # IDs should have underscores instead of spaces
            assert " " not in section.id
            # IDs should be limited in length
            assert len(section.id) <= 50

    def test_section_levels(self, content_processor: ContentProcessor):
        """Test that section levels are correctly assigned."""
        markdown = """# Level 1

## Level 2

### Level 3

Art. 1. Article (should be level 2)

Rozdział 1 Chapter (should be level 1)"""

        sections = content_processor.index_sections(markdown)

        # Find specific sections and check their levels
        art_sections = [s for s in sections if "Art." in s.title]
        if art_sections:
            assert art_sections[0].level == 2

        rozdzial_sections = [s for s in sections if "Rozdział" in s.title]
        if rozdzial_sections:
            assert rozdzial_sections[0].level == 1

    def test_section_with_dział_pattern(self, content_processor: ContentProcessor):
        """Test indexing sections with DZIAŁ pattern."""
        markdown = """DZIAŁ I General Part

Content of part 1.

DZIAŁ II Special Part

Content of part 2."""

        sections = content_processor.index_sections(markdown)

        dział_sections = [s for s in sections if "DZIAŁ" in s.title]
        assert len(dział_sections) >= 2
        assert dział_sections[0].level == 1


class TestSection:
    """Tests for Section dataclass."""

    def test_section_creation(self):
        """Test creating a Section instance."""
        section = Section(
            id="art_1",
            title="Art. 1.",
            level=2,
            start_pos=100,
            end_pos=200,
            content="Content here",
        )

        assert section.id == "art_1"
        assert section.title == "Art. 1."
        assert section.level == 2
        assert section.start_pos == 100
        assert section.end_pos == 200
        assert section.content == "Content here"

    def test_section_optional_fields(self):
        """Test Section with optional fields."""
        section = Section(
            id="test",
            title="Test",
            level=1,
            start_pos=0,
        )

        assert section.end_pos is None
        assert section.content == ""
