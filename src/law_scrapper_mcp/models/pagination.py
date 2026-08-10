"""Models shared by paginated tool outputs."""

from enum import StrEnum

from pydantic import BaseModel


class PageUnit(StrEnum):
    """Unit represented by a page."""

    ITEMS = "items"
    CHARACTERS = "characters"


class PageInfo(BaseModel):
    """Metadata describing one bounded response page."""

    limit: int
    offset: int
    returned_count: int
    total_count: int
    was_truncated: bool
    next_offset: int | None
    unit: PageUnit


DEFAULT_ITEM_LIMIT = 20
DEFAULT_SECTION_CHAR_LIMIT = 10_000


def empty_item_page_info(*, limit: int = DEFAULT_ITEM_LIMIT) -> PageInfo:
    """Return a zero-count item page placeholder."""
    return PageInfo(
        limit=limit,
        offset=0,
        returned_count=0,
        total_count=0,
        was_truncated=False,
        next_offset=None,
        unit=PageUnit.ITEMS,
    )


def empty_character_page_info(*, limit: int = DEFAULT_SECTION_CHAR_LIMIT) -> PageInfo:
    """Return a zero-count character page placeholder."""
    return PageInfo(
        limit=limit,
        offset=0,
        returned_count=0,
        total_count=0,
        was_truncated=False,
        next_offset=None,
        unit=PageUnit.CHARACTERS,
    )
