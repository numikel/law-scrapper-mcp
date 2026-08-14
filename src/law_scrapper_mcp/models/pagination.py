"""Models shared by paginated tool outputs."""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator

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
    next_offset: int | None = Field(default=None, ge=0)
    unit: PageUnit

    @model_validator(mode="after")
    def validate_page_consistency(self) -> Self:
        """Ensure pagination metadata is internally consistent."""
        if self.returned_count > self.limit:
            raise ValueError("returned_count cannot exceed limit")

        available = max(self.total_count - self.offset, 0)
        if self.returned_count > available:
            raise ValueError("returned_count cannot exceed remaining items for the current offset")

        end = min(self.offset + self.returned_count, self.total_count)
        expected_truncated = end < self.total_count
        if self.was_truncated != expected_truncated:
            raise ValueError("was_truncated is inconsistent with offset, returned_count, and total_count")

        expected_next = end if self.limit > 0 and expected_truncated else None
        if self.next_offset != expected_next:
            raise ValueError("next_offset is inconsistent with offset, returned_count, and total_count")

        return self


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
    if section is not None:
        return empty_character_page_info()
    return empty_item_page_info()
