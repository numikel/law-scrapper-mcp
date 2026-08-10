"""Shared validation and slicing for bounded tool responses."""

from collections.abc import Sequence

from law_scrapper_mcp.models.pagination import PageInfo, PageUnit

DEFAULT_ITEM_LIMIT = 20
MAX_ITEM_LIMIT = 100
MAX_CONTEXT_CHARS = 2_000
DEFAULT_SECTION_CHAR_LIMIT = 10_000
MAX_SECTION_CHAR_LIMIT = 50_000


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


def paginate_items[T](items: Sequence[T], *, limit: int, offset: int) -> tuple[list[T], PageInfo]:
    """Return one item page and its metadata."""
    total = len(items)
    page = list(items[offset : offset + limit]) if limit > 0 else []
    return page, _page_info(
        limit=limit,
        offset=offset,
        returned=len(page),
        total=total,
        unit=PageUnit.ITEMS,
    )


def paginate_text(text: str, *, limit: int, offset: int) -> tuple[str, PageInfo]:
    """Return one character page and its metadata."""
    page = text[offset : offset + limit] if limit > 0 else ""
    return page, _page_info(
        limit=limit,
        offset=offset,
        returned=len(page),
        total=len(text),
        unit=PageUnit.CHARACTERS,
    )
