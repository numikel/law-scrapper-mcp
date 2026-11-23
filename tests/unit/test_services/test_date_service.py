"""Tests for DateService."""

from datetime import datetime
from unittest.mock import patch

import pytest

from law_scrapper_mcp.services.date_service import DateService


def _invalid_date_message(value: str) -> str:
    return (
        f"Nieprawidłowy format daty: '{value}'. "
        f"Obsługiwane formaty: YYYY-MM-DD (np. '2024-01-15'), "
        f"YYYY-MM (np. '2024-01', domyślnie 1. dzień), "
        f"YYYY (np. '2024', domyślnie 1 stycznia)."
    )


@pytest.mark.parametrize(
    ("base_date", "expected"),
    [
        ("2024", "2024-01-01"),
        ("2024-02", "2024-02-01"),
        ("2024-02-29", "2024-02-29"),
    ],
)
def test_calculate_accepts_supported_date_formats(base_date: str, expected: str) -> None:
    assert DateService().calculate(base_date=base_date).base_date == expected


def test_calculate_normalizes_string_offsets() -> None:
    result = DateService().calculate(days="14", months="1", years="1", base_date="2024-01-01")

    assert result.calculated_date == "2025-02-15"
    assert result.days_offset == 14
    assert result.months_offset == 1
    assert result.years_offset == 1


def test_calculate_rejects_invalid_date_format_with_full_message() -> None:
    with pytest.raises(ValueError) as exc_info:
        DateService().calculate(base_date="01/02/2024")

    assert str(exc_info.value) == _invalid_date_message("01/02/2024")


def test_calculate_rejects_invalid_calendar_date_with_full_message() -> None:
    with pytest.raises(ValueError) as exc_info:
        DateService().calculate(base_date="2024-02-30")

    assert str(exc_info.value) == _invalid_date_message("2024-02-30")


def test_calculate_rejects_empty_base_date() -> None:
    with pytest.raises(ValueError) as exc_info:
        DateService().calculate(base_date="")

    assert str(exc_info.value) == _invalid_date_message("")


def test_calculate_uses_clock_seam_when_base_date_omitted() -> None:
    fixed = datetime(2024, 6, 15, 12, 0, 0)

    with patch.object(DateService, "_current_datetime", return_value=fixed):
        result = DateService().calculate()

    assert result.base_date == "2024-06-15"
    assert result.calculated_date == "2024-06-15"
    assert result.description == "Data bazowa: 2024-06-15"


@pytest.mark.parametrize("offset_param", ["days", "months", "years"])
def test_calculate_rejects_boolean_offsets(offset_param: str) -> None:
    with pytest.raises(ValueError, match="Nieprawidłowa wartość przesunięcia"):
        DateService().calculate(**{offset_param: True}, base_date="2024-01-01")


def test_calculate_normalizes_non_numeric_string_offsets_to_zero() -> None:
    result = DateService().calculate(days="abc", base_date="2024-01-01")

    assert result.days_offset == 0
    assert result.calculated_date == "2024-01-01"
    assert result.description == "Data bazowa: 2024-01-01"


def test_calculate_handles_month_end_arithmetic() -> None:
    result = DateService().calculate(months=1, base_date="2024-01-31")

    assert result.calculated_date == "2024-02-29"


def test_calculate_handles_leap_year_arithmetic() -> None:
    result = DateService().calculate(years=1, base_date="2024-02-29")

    assert result.calculated_date == "2025-02-28"


@pytest.mark.parametrize(
    ("years", "expected_fragment"),
    [
        (12, "12 lat po"),
        (13, "13 lat po"),
        (14, "14 lat po"),
        (22, "22 lata po"),
        (23, "23 lata po"),
        (24, "24 lata po"),
        (25, "25 lat po"),
    ],
)
def test_calculate_uses_correct_polish_quantity_inflection(years: int, expected_fragment: str) -> None:
    result = DateService().calculate(years=years, base_date="2024-01-01")

    assert expected_fragment in result.description


def test_calculate_renders_mixed_sign_offsets_with_per_component_direction() -> None:
    result = DateService().calculate(months=-1, days=1, base_date="2024-06-15")

    assert result.calculated_date == "2024-05-16"
    assert result.description == "1 miesiąc przed, 1 dzień po 2024-06-15"


# Deadline arithmetic is delegated to `dateutil.relativedelta`, so a release that changes how
# it clamps month ends or combines components would silently move every deadline the
# `calculate_legal_date` tool reports. Each row is a term an agent actually asks for
# (vacatio legis, appeal windows, statutory years) at a calendar edge where a naive
# implementation and relativedelta disagree.
@pytest.mark.parametrize(
    ("base_date", "offsets", "expected"),
    [
        # 14-day vacatio legis crossing a year boundary
        ("2024-12-20", {"days": 14}, "2025-01-03"),
        # 30-day term running through a leap February
        ("2024-02-15", {"days": 30}, "2024-03-16"),
        # a whole leap year counted in days
        ("2024-01-01", {"days": 366}, "2025-01-01"),
        # one month from the 31st clamps to a 30-day month
        ("2024-03-31", {"months": 1}, "2024-04-30"),
        # one month back from the 31st clamps to the leap-day
        ("2024-03-31", {"months": -1}, "2024-02-29"),
        # twelve months from 28 February stays on the 28th, never drifts to the leap day
        ("2023-02-28", {"months": 12}, "2024-02-28"),
        # a year back from the leap day lands on 28 February
        ("2024-02-29", {"years": -1}, "2023-02-28"),
        # years and months are applied together before the day is clamped:
        # 2025-03-29, not 2025-03-28 as "add a year, clamp, then add a month" would give
        ("2024-02-29", {"years": 1, "months": 1}, "2025-03-29"),
        # months are applied before days: Jan 30 + 1 month = Feb 29, + 1 day = Mar 1
        # (adding the day first would give Jan 31 + 1 month = Feb 29)
        ("2024-01-30", {"months": 1, "days": 1}, "2024-03-01"),
        # a negative day count that crosses a month and year boundary
        ("2025-01-10", {"days": -14}, "2024-12-27"),
    ],
)
def test_calculate_legal_deadlines_at_calendar_edges(base_date: str, offsets: dict[str, int], expected: str) -> None:
    result = DateService().calculate(base_date=base_date, **offsets)

    assert result.calculated_date == expected
    assert result.base_date == base_date
