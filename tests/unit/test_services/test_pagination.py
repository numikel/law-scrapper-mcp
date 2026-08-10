"""Tests for shared pagination helpers."""

import pytest
from pydantic import ValidationError

from law_scrapper_mcp.models.pagination import (
    DEFAULT_ITEM_LIMIT,
    DEFAULT_SECTION_CHAR_LIMIT,
    PageInfo,
    PageUnit,
    empty_character_page_info,
    empty_item_page_info,
    error_page_info_for_content,
)
from law_scrapper_mcp.services.pagination import (
    effective_limit,
    full_item_page,
    full_text_page,
    paginate_items,
    paginate_text,
    parse_non_negative,
)


def test_effective_limit_applies_default_and_ceiling() -> None:
    assert effective_limit(None, default=20, maximum=100) == 20
    assert effective_limit("250", default=20, maximum=100) == 100


@pytest.mark.parametrize(("value", "name"), [("-1", "limit"), (-1, "offset")])
def test_negative_values_are_rejected(value: str | int, name: str) -> None:
    with pytest.raises(ValueError, match=f"Parametr '{name}' nie może być ujemny"):
        parse_non_negative(value, name=name, default=0)


@pytest.mark.parametrize(
    ("paginator", "args"),
    [
        (paginate_items, ([1, 2, 3],)),
        (paginate_text, ("abc",)),
    ],
)
def test_paginators_reject_negative_limit(
    paginator: object,
    args: tuple[object, ...],
) -> None:
    with pytest.raises(ValueError, match="Parametr 'limit' nie może być ujemny"):
        paginator(*args, limit=-1, offset=0)  # type: ignore[operator]


@pytest.mark.parametrize(
    ("paginator", "args"),
    [
        (paginate_items, ([1, 2, 3],)),
        (paginate_text, ("abc",)),
    ],
)
def test_paginators_reject_negative_offset(
    paginator: object,
    args: tuple[object, ...],
) -> None:
    with pytest.raises(ValueError, match="Parametr 'offset' nie może być ujemny"):
        paginator(*args, limit=1, offset=-1)  # type: ignore[operator]


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


def test_partial_last_page_is_not_truncated() -> None:
    page, info = paginate_items(list(range(5)), limit=3, offset=3)

    assert page == [3, 4]
    assert info.returned_count == 2
    assert info.was_truncated is False
    assert info.next_offset is None


def test_offset_at_total_returns_empty_non_truncated_page() -> None:
    page, info = paginate_items([1, 2, 3], limit=2, offset=3)

    assert page == []
    assert info.returned_count == 0
    assert info.total_count == 3
    assert info.was_truncated is False
    assert info.next_offset is None


def test_offset_beyond_total_returns_empty_non_truncated_page() -> None:
    page, info = paginate_items([1, 2, 3], limit=2, offset=10)

    assert page == []
    assert info.returned_count == 0
    assert info.total_count == 3
    assert info.was_truncated is False
    assert info.next_offset is None


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


def test_full_item_page_matches_payload_without_truncating() -> None:
    items = list(range(25))
    page, info = full_item_page(items)

    assert page == items
    assert info.returned_count == 25
    assert info.total_count == 25
    assert info.limit == 25
    assert info.was_truncated is False


def test_full_item_page_empty_uses_default_limit() -> None:
    page, info = full_item_page([])

    assert page == []
    assert info.limit == DEFAULT_ITEM_LIMIT
    assert info.returned_count == 0
    assert info.total_count == 0


def test_full_text_page_matches_payload_without_truncating() -> None:
    text = "abcdefghij"
    page, info = full_text_page(text)

    assert page == text
    assert info.returned_count == 10
    assert info.limit == 10
    assert info.unit == PageUnit.CHARACTERS


def test_full_text_page_empty_uses_section_default_limit() -> None:
    page, info = full_text_page("")

    assert page == ""
    assert info.limit == DEFAULT_SECTION_CHAR_LIMIT
    assert info.returned_count == 0


def test_page_info_rejects_negative_counters() -> None:
    with pytest.raises(ValidationError):
        PageInfo(
            limit=-1,
            offset=0,
            returned_count=0,
            total_count=0,
            was_truncated=False,
            next_offset=None,
            unit=PageUnit.ITEMS,
        )


def test_empty_item_page_info_uses_items_unit_and_default_limit() -> None:
    info = empty_item_page_info()

    assert info.unit == PageUnit.ITEMS
    assert info.limit == DEFAULT_ITEM_LIMIT
    assert info.returned_count == 0


def test_empty_character_page_info_uses_characters_unit_and_default_limit() -> None:
    info = empty_character_page_info()

    assert info.unit == PageUnit.CHARACTERS
    assert info.limit == DEFAULT_SECTION_CHAR_LIMIT
    assert info.returned_count == 0


def test_content_error_page_info_is_section_aware() -> None:
    assert error_page_info_for_content(section=None).unit == PageUnit.ITEMS
    assert error_page_info_for_content(section="Art. 1").unit == PageUnit.CHARACTERS


def test_content_error_page_info_treats_empty_section_as_characters() -> None:
    assert error_page_info_for_content(section="").unit == PageUnit.CHARACTERS


@pytest.mark.parametrize(
    "invalid_page_info",
    [
        {
            "limit": 3,
            "offset": 0,
            "returned_count": 3,
            "total_count": 7,
            "was_truncated": True,
            "next_offset": -1,
            "unit": PageUnit.ITEMS,
        },
        {
            "limit": 3,
            "offset": 0,
            "returned_count": 3,
            "total_count": 7,
            "was_truncated": False,
            "next_offset": 3,
            "unit": PageUnit.ITEMS,
        },
        {
            "limit": 3,
            "offset": 0,
            "returned_count": 3,
            "total_count": 7,
            "was_truncated": True,
            "next_offset": None,
            "unit": PageUnit.ITEMS,
        },
        {
            "limit": 3,
            "offset": 0,
            "returned_count": 3,
            "total_count": 7,
            "was_truncated": True,
            "next_offset": 99,
            "unit": PageUnit.ITEMS,
        },
        {
            "limit": 0,
            "offset": 0,
            "returned_count": 1,
            "total_count": 2,
            "was_truncated": True,
            "next_offset": None,
            "unit": PageUnit.ITEMS,
        },
        {
            "limit": 5,
            "offset": 10,
            "returned_count": 1,
            "total_count": 2,
            "was_truncated": False,
            "next_offset": None,
            "unit": PageUnit.ITEMS,
        },
    ],
)
def test_page_info_rejects_inconsistent_construction(invalid_page_info: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PageInfo(**invalid_page_info)


def test_page_info_rejects_returned_count_above_zero_limit() -> None:
    with pytest.raises(ValidationError):
        PageInfo(
            limit=0,
            offset=0,
            returned_count=1,
            total_count=2,
            was_truncated=True,
            next_offset=None,
            unit=PageUnit.ITEMS,
        )


def test_page_info_rejects_returned_count_beyond_remaining_items() -> None:
    with pytest.raises(ValidationError):
        PageInfo(
            limit=5,
            offset=10,
            returned_count=1,
            total_count=2,
            was_truncated=False,
            next_offset=None,
            unit=PageUnit.ITEMS,
        )
