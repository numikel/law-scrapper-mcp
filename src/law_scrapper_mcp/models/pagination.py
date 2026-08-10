"""Models shared by paginated tool outputs."""

from enum import StrEnum

from pydantic import BaseModel, Field

DEFAULT_ITEM_LIMIT = 20
MAX_ITEM_LIMIT = 100
MAX_CONTEXT_CHARS = 2_000
DEFAULT_SECTION_CHAR_LIMIT = 10_000
MAX_SECTION_CHAR_LIMIT = 50_000


class PageUnit(StrEnum):
    """Unit represented by a page."""

    ITEMS = "items"
    CHARACTERS = "characters"


class PageInfo(BaseModel):
    """Metadata describing one bounded response page."""

    limit: int = Field(ge=0)
    offset: int = Field(ge=0)
    returned_count: int = Field(ge=0)
    total_count: int = Field(ge=0)
    was_truncated: bool
    next_offset: int | None
    unit: PageUnit


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


def error_page_info_for_content(*, section: str | None) -> PageInfo:
    """Return the zero-count page placeholder for read_act_content errors."""
    if section:
        return empty_character_page_info()
    return empty_item_page_info()
