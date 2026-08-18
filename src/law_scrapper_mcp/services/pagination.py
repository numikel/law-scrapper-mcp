"""Shared validation and slicing for bounded tool responses."""

from collections.abc import Sequence

from law_scrapper_mcp.models.pagination import (
    DEFAULT_ITEM_LIMIT,
    DEFAULT_SECTION_CHAR_LIMIT,
    MAX_CONTEXT_CHARS,
    MAX_ITEM_LIMIT,
    MAX_SECTION_CHAR_LIMIT,
    PageInfo,
    PageUnit,
)

__all__ = [
    "DEFAULT_ITEM_LIMIT",
    "DEFAULT_SECTION_CHAR_LIMIT",
    "MAX_CONTEXT_CHARS",
    "MAX_ITEM_LIMIT",
    "MAX_SECTION_CHAR_LIMIT",
    "effective_limit",
    "full_item_page",
    "full_text_page",
    "item_page_info",
    "paginate_items",
    "paginate_text",
    "parse_non_negative",
]


def parse_non_negative(value: str | int | None, *, name: str, default: int) -> int:
    """Parse an integer parameter and reject negative values."""
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Parametr '{name}' musi być liczbą całkowitą.") from exc
    if parsed < 0:
        raise ValueError(f"Parametr '{name}' nie może być ujemny.")
    return parsed


def effective_limit(value: str | int | None, *, default: int, maximum: int) -> int:
    """Apply a default and clamp a non-negative limit."""
    return min(parse_non_negative(value, name="limit", default=default), maximum)


def _validated_page_bounds(*, limit: int, offset: int) -> tuple[int, int]:
    return (
        parse_non_negative(limit, name="limit", default=0),
        parse_non_negative(offset, name="offset", default=0),
    )


def _page_info(*, limit: int, offset: int, returned: int, total: int, unit: PageUnit) -> PageInfo:
    end = min(offset + returned, total)
    was_truncated = end < total
    return PageInfo(
        limit=limit,
        offset=offset,
        returned_count=returned,
        total_count=total,
        was_truncated=was_truncated,
        next_offset=end if limit > 0 and was_truncated else None,
        unit=unit,
    )


def item_page_info(*, limit: int, offset: int, returned: int, total: int) -> PageInfo:
    """Describe an item page whose window was produced elsewhere.

    `paginate_items` both slices and describes. The Sejm API applies limit and
    offset server-side, so the slice already happened upstream and only the
    description is needed here.
    """
    return _page_info(limit=limit, offset=offset, returned=returned, total=total, unit=PageUnit.ITEMS)


def paginate_items[T](items: Sequence[T], *, limit: int, offset: int) -> tuple[list[T], PageInfo]:
    """Return one item page and its metadata."""
    page_limit, page_offset = _validated_page_bounds(limit=limit, offset=offset)
    total = len(items)
    page = list(items[page_offset : page_offset + page_limit]) if page_limit > 0 else []
    return page, _page_info(
        limit=page_limit,
        offset=page_offset,
        returned=len(page),
        total=total,
        unit=PageUnit.ITEMS,
    )


def paginate_text(text: str, *, limit: int, offset: int) -> tuple[str, PageInfo]:
    """Return one character page and its metadata."""
    page_limit, page_offset = _validated_page_bounds(limit=limit, offset=offset)
    page = text[page_offset : page_offset + page_limit] if page_limit > 0 else ""
    return page, _page_info(
        limit=page_limit,
        offset=page_offset,
        returned=len(page),
        total=len(text),
        unit=PageUnit.CHARACTERS,
    )


def full_item_page[T](items: Sequence[T]) -> tuple[list[T], PageInfo]:
    """Return the full current item payload with truthful page metadata."""
    page_limit = len(items) if items else DEFAULT_ITEM_LIMIT
    return paginate_items(items, limit=page_limit, offset=0)


def full_text_page(text: str) -> tuple[str, PageInfo]:
    """Return the full current text payload with truthful page metadata."""
    page_limit = len(text) if text else DEFAULT_SECTION_CHAR_LIMIT
    return paginate_text(text, limit=page_limit, offset=0)
