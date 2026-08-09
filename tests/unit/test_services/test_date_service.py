"""Tests for DateService."""

import pytest

from law_scrapper_mcp.services.date_service import DateService


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


def test_calculate_rejects_invalid_date() -> None:
    with pytest.raises(ValueError, match="Nieprawidłowy format daty"):
        DateService().calculate(base_date="01/02/2024")
