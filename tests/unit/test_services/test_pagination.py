"""Tests for shared pagination helpers."""

import pytest

from law_scrapper_mcp.models.pagination import PageUnit
from law_scrapper_mcp.services.pagination import effective_limit, paginate_items, paginate_text, parse_non_negative


def test_effective_limit_applies_default_and_ceiling() -> None:
    assert effective_limit(None, default=20, maximum=100) == 20
    assert effective_limit("250", default=20, maximum=100) == 100


@pytest.mark.parametrize(("value", "name"), [("-1", "limit"), (-1, "offset")])
def test_negative_values_are_rejected(value: str | int, name: str) -> None:
    with pytest.raises(ValueError, match=f"Parametr '{name}' nie może być ujemny"):
        parse_non_negative(value, name=name, default=0)


def test_middle_item_page_has_correct_metadata() -> None:
    page, info = paginate_items(list(range(7)), limit=3, offset=3)

    assert page == [3, 4, 5]
    assert info.model_dump() == {
        "limit": 3,
        "offset": 3,
        "returned_count": 3,
        "total_count": 7,
        "was_truncated": True,
        "next_offset": 6,
        "unit": PageUnit.ITEMS,
    }


def test_zero_limit_is_empty_and_non_progressing() -> None:
    page, info = paginate_items([1, 2], limit=0, offset=0)

    assert page == []
    assert info.total_count == 2
    assert info.was_truncated is True
    assert info.next_offset is None


def test_text_pagination_counts_characters() -> None:
    page, info = paginate_text("abcdefghij", limit=4, offset=4)

    assert page == "efgh"
    assert info.returned_count == 4
    assert info.total_count == 10
    assert info.unit == PageUnit.CHARACTERS
    assert info.next_offset == 8
