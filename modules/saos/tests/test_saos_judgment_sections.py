"""The text of a judgment as Markdown and sections: tags, anonymisation markers, headings, plain text.

The shapes come from the skeleton recordings (see `saos_reading_testing`). Where a test needs words it derives a
small text with placeholder words, because a skeleton has none.
"""

from __future__ import annotations

import pytest
from saos_reading_testing import NESTED_ID, REASONS_ID, SENTENCE_ID, SUPREME_ID, skeleton

from law_scrapper_mcp.services.content_processor import ContentProcessor
from law_scrapper_mcp_saos import reading

# A common-court text in the recorded shape: two court headings, anonymisation spans, a line that starts with `Art.`.
HTML = (
    '<div><h2>WYROK</h2><p>Sąd <span class="anon-block">(...)</span> zasądza od '
    '<span class="anon-block">D. P. (1)</span> kwotę.</p>'
    "<h2>UZASADNIENIE</h2><p>Art. 207 k.p. stanowi zasadę.</p><p>Dalej tekst.</p></div>"
)


def text_of(source_id: int) -> str:
    return str(skeleton(source_id)["data"]["textContent"])


def titles(markdown: str) -> list[str]:
    return [section.title for section in reading.index_sections(markdown)]


class TestConversion:
    def test_the_anonymisation_markers_keep_their_text_and_lose_the_tag(self) -> None:
        markdown = reading.to_markdown(HTML)

        assert "(...)" in markdown
        assert "D. P. (1)" in markdown
        assert "anon-block" not in markdown
        assert "<span" not in markdown

    def test_headings_become_level_two_headings(self) -> None:
        markdown = reading.to_markdown(HTML)

        assert "## WYROK" in markdown
        assert "## UZASADNIENIE" in markdown

    def test_a_text_without_a_tag_is_plain_text(self) -> None:
        assert reading.has_markup(HTML) is True
        assert reading.has_markup("zwykły tekst z uwagą: 2 < 3 i art. 5") is False
        assert reading.has_markup(text_of(SUPREME_ID)) is False

    def test_plain_text_keeps_its_paragraphs(self) -> None:
        """`markdownify` would drop the blank lines of a plain text, so it is not used for one."""
        plain = "Pierwszy akapit\nlinia druga\r\n\r\n\r\n\r\nDrugi akapit\n\n\nTrzeci"

        assert reading.to_markdown(plain) == "Pierwszy akapit\nlinia druga\n\nDrugi akapit\n\nTrzeci"
        assert ContentProcessor().html_to_markdown(plain).count("\n\n") < reading.to_markdown(plain).count("\n\n")

    def test_the_recorded_supreme_court_text_loses_no_word(self) -> None:
        text = text_of(SUPREME_ID)

        assert reading.to_markdown(text).split() == text.split()

    @pytest.mark.asyncio
    async def test_the_async_conversion_gives_the_same_result(self) -> None:
        assert await reading.to_markdown_async(HTML) == reading.to_markdown(HTML)


class TestSections:
    def test_a_section_starts_at_each_level_two_heading(self) -> None:
        sections = reading.index_sections(reading.to_markdown(HTML))

        assert [section.title for section in sections] == ["WYROK", "UZASADNIENIE"]
        assert [section.level for section in sections] == [2, 2]
        assert "(...)" in sections[0].content
        assert sections[1].content.startswith("## UZASADNIENIE")

    def test_an_art_line_does_not_start_a_section(self) -> None:
        markdown = reading.to_markdown(HTML)

        assert "Art. 207 k.p. stanowi zasadę." in markdown
        assert titles(markdown) == ["WYROK", "UZASADNIENIE"]
        # What the core's indexer would have done to the same text, which is why it is not used here.
        assert len(ContentProcessor().index_sections(markdown)) == 3

    def test_a_text_without_headings_is_one_section_that_covers_all_of_it(self) -> None:
        markdown = reading.to_markdown("<p>Pierwszy akapit.</p><p>Art. 1 drugi akapit.</p>")

        sections = reading.index_sections(markdown)

        assert [(section.title, section.level) for section in sections] == [("Treść", 1)]
        assert (sections[0].start_pos, sections[0].end_pos) == (0, len(markdown))
        assert ContentProcessor().index_sections("Pierwszy akapit.\n\nDrugi akapit.") == []

    def test_the_text_before_the_first_heading_is_the_section_tresc(self) -> None:
        markdown = "Sygn. akt\n\n## WYROK\n\ntreść\n\n## UZASADNIENIE\n\nuzasadnienie"

        assert titles(markdown) == ["Treść", "WYROK", "UZASADNIENIE"]

    def test_no_section_tresc_when_the_text_starts_with_a_heading(self) -> None:
        assert titles("## WYROK\n\ntreść") == ["WYROK"]

    def test_the_ranges_are_disjoint_and_cover_the_whole_text(self) -> None:
        markdown = reading.to_markdown(HTML)
        sections = reading.index_sections(markdown)

        assert sections[0].start_pos == 0
        assert sections[-1].end_pos == len(markdown)
        assert all(a.end_pos == b.start_pos for a, b in zip(sections, sections[1:], strict=False))

    def test_two_headings_that_read_alike_get_different_ids(self) -> None:
        sections = reading.index_sections("## UZASADNIENIE\n\na\n\n## UZASADNIENIE\n\nb")

        assert [section.id for section in sections] == ["UZASADNIENIE", "UZASADNIENIE_2"]

    def test_only_level_two_headings_make_sections(self) -> None:
        assert titles("## WYROK\n\n##### W IMIENIU\n\ntreść\n\n### Podpunkt") == ["WYROK"]

    @pytest.mark.parametrize(
        ("source_id", "expected"),
        [
            (SENTENCE_ID, ["Treść", "WYROK"]),
            (REASONS_ID, ["Treść", "UZASADNIENIE"]),
            (NESTED_ID, ["Treść", "WYROK", "UZASADNIENIE"]),
            (SUPREME_ID, ["Treść"]),
        ],
    )
    def test_the_recorded_skeletons_give_the_court_headings(self, source_id: int, expected: list[str]) -> None:
        assert titles(reading.to_markdown(text_of(source_id))) == expected


class TestReasoningHeading:
    def test_the_heading_is_found_whatever_its_case_or_trailing_dot(self) -> None:
        assert reading.has_reasoning_heading("## UZASADNIENIE\n\nx") is True
        assert reading.has_reasoning_heading("## Uzasadnienie.\n\nx") is True

    def test_a_lower_level_heading_or_a_word_in_a_paragraph_is_not_the_section(self) -> None:
        assert reading.has_reasoning_heading("### UZASADNIENIE\n\nx") is False
        assert reading.has_reasoning_heading("## WYROK\n\nzobacz uzasadnienie niżej") is False

    def test_the_recorded_sentence_has_none_and_the_other_records_have_one(self) -> None:
        assert reading.has_reasoning_heading(reading.to_markdown(text_of(SENTENCE_ID))) is False
        assert reading.has_reasoning_heading(reading.to_markdown(text_of(REASONS_ID))) is True
        assert reading.has_reasoning_heading(reading.to_markdown(text_of(NESTED_ID))) is True
        assert reading.has_reasoning_heading(reading.to_markdown(text_of(SUPREME_ID))) is False


class TestComposition:
    def test_the_reasons_follow_the_sentence_under_their_own_heading(self) -> None:
        sentence = reading.to_markdown(text_of(SENTENCE_ID))
        reasons = reading.to_markdown(text_of(REASONS_ID))

        merged = reading.with_reasons(sentence, reasons)

        assert merged == f"{sentence}\n\n{reasons}"
        assert titles(merged) == ["Treść", "WYROK", "UZASADNIENIE"]

    def test_a_reasons_text_without_the_heading_gets_one(self) -> None:
        merged = reading.with_reasons("## WYROK\n\na", "treść bez nagłówka")

        assert titles(merged) == ["WYROK", "UZASADNIENIE"]
        assert merged.count("## UZASADNIENIE") == 1

    def test_the_server_note_is_a_last_section_that_says_it_is_not_the_judgment(self) -> None:
        marked = reading.with_no_reasoning_note("## WYROK\n\na")

        sections = reading.index_sections(marked)

        assert [section.title for section in sections] == ["WYROK", "[Uwaga serwera] Uzasadnienie"]
        assert sections[-1].id == "Uwaga_serwera_Uzasadnienie"
        assert sections[-1].content.endswith(
            "Brak uzasadnienia w źródle: SAOS nie podaje uzasadnienia tego orzeczenia. "
            "To uwaga dodana przez serwer, nie tekst orzeczenia."
        )
        # The note must not pass for the reasoning itself.
        assert reading.has_reasoning_heading(marked) is False
