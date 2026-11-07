"""The text of a SAOS judgment as the Markdown and sections the document store holds.

Pure functions, no network. A common-court text is HTML and goes through the same conversion as the text
of an act. The Supreme Court text is plain text (no tag at all, line breaks only); `markdownify` would
drop its blank lines, so it is kept as written.

Sections are not made with the core's `index_sections`: that one starts a section at every `Art.` line and
gives no section at all to a text without headings, and a judgment is full of lines that start with
`Art. 207 k.p.` and often has no heading. Here a section starts at each level-2 heading (`## WYROK`,
`## UZASADNIENIE`), and whatever comes before the first heading, or the whole text when it has none, is
the section `Treść`.
"""

from __future__ import annotations

import asyncio
import re

from law_scrapper_mcp.services.content_processor import ContentProcessor, Section

FALLBACK_TITLE = "Treść"
REASONING_TITLE = "UZASADNIENIE"
SERVER_NOTE_TITLE = "[Uwaga serwera] Uzasadnienie"
NO_REASONING_TEXT = (
    "Brak uzasadnienia w źródle: SAOS nie podaje uzasadnienia tego orzeczenia. "
    "To uwaga dodana przez serwer, nie tekst orzeczenia."
)

_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>")
# The rest of the line is taken whole and stripped by the callers: a lazy `(.+?)[ \t]*$` backtracks
# quadratically on a heading followed by a long run of spaces and then a character.
_HEADING = re.compile(r"^##[ \t]+([^\n]*)$", re.MULTILINE)
MAX_SECTIONS = 500
_BLANK_RUNS = re.compile(r"\n{3,}")
_SLUG_DROP = re.compile(r"[^\w\s.-]")
_SLUG_MAX = 50


def has_markup(text: str) -> bool:
    """Whether the text holds at least one HTML tag."""
    return _TAG.search(text) is not None


def to_markdown(text: str) -> str:
    """Markdown of a judgment text: HTML converted like an act, plain text kept as written."""
    if has_markup(text):
        return ContentProcessor().html_to_markdown(text)
    plain = text.replace("\r\n", "\n").replace("\r", "\n")
    return _BLANK_RUNS.sub("\n\n", plain).strip()


async def to_markdown_async(text: str) -> str:
    """`to_markdown` off the event loop: the conversion is synchronous, as it is for an act."""
    return await asyncio.to_thread(to_markdown, text)


def _slug(title: str) -> str:
    slug = _SLUG_DROP.sub("", title).strip().replace(" ", "_")[:_SLUG_MAX]
    return slug or "sekcja"


def _unique(slug: str, used: set[str], last_number: dict[str, int]) -> str:
    """`slug`, or `slug_N` when it is taken; `last_number` remembers where each slug stopped counting.

    Starting again from 2 for every repeat would cost O(k²) for k identical headings, and the text is
    third-party: a few thousand `## A` lines would hold the event loop for seconds.
    """
    candidate = slug
    number = last_number.get(slug, 1)
    while candidate in used:
        number += 1
        candidate = f"{slug}_{number}"
    last_number[slug] = number
    used.add(candidate)
    return candidate


def index_sections(markdown: str, *, max_sections: int = MAX_SECTIONS) -> list[Section]:
    """Sections of a judgment: one per level-2 heading, plus `Treść` for the text before the first one.

    The ranges are disjoint and cover the whole text, which is what the document store requires. Section
    ids are unique even when two headings read alike (a sentence and its reasons both carry a caption).

    Raises:
        ValueError: The text has more than `max_sections` sections (Polish message). A real judgment has a
            handful; a table of contents of thousands is not one an agent can use. The parameter exists so
            that a test can index a text of any size.
    """
    headings = [(match.start(), match.group(1).strip()) for match in _HEADING.finditer(markdown)]
    starts: list[tuple[int, str, int]] = []
    if not headings or markdown[: headings[0][0]].strip():
        starts.append((0, FALLBACK_TITLE, 1))
    starts.extend((position, title, 2) for position, title in headings)
    if len(starts) > max_sections:
        raise ValueError(
            f"Tekst orzeczenia ma {len(starts)} sekcji, więcej niż dopuszczalne {max_sections}, więc nie został wczytany."
        )

    used: set[str] = set()
    last_number: dict[str, int] = {}
    sections: list[Section] = []
    for index, (start, title, level) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(markdown)
        sections.append(
            Section(
                id=_unique(_slug(title), used, last_number),
                title=title,
                level=level,
                start_pos=start,
                end_pos=end,
                content=markdown[start:end].strip(),
            )
        )
    return sections


def _is_reasoning_title(title: str) -> bool:
    return title.strip(" .").casefold() == REASONING_TITLE.casefold()


def has_reasoning_heading(markdown: str) -> bool:
    """Whether a level-2 heading of the text reads `UZASADNIENIE`."""
    return any(_is_reasoning_title(match.group(1)) for match in _HEADING.finditer(markdown))


def with_reasons(sentence: str, reasons: str) -> str:
    """The sentence followed by the text of its reasons record, under a `UZASADNIENIE` heading.

    The reasons record normally has that heading itself; when it does not, one is put in front, so the
    section is always there. The caption line a reasons record starts with stays where it is.
    """
    if not has_reasoning_heading(reasons):
        reasons = f"## {REASONING_TITLE}\n\n{reasons}"
    return f"{sentence}\n\n{reasons}"


def with_no_reasoning_note(markdown: str) -> str:
    """The text followed by a last section that says the source has no reasoning, as a server note."""
    return f"{markdown}\n\n## {SERVER_NOTE_TITLE}\n\n{NO_REASONING_TEXT}"
