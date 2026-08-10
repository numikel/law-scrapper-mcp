"""Domain service for legal date calculations."""

import re
from datetime import datetime

from dateutil.relativedelta import relativedelta  # type: ignore[import-untyped]

from law_scrapper_mcp.models.tool_outputs import DateOutput


class DateService:
    """Calculate legal dates without MCP dependencies."""

    def calculate(
        self,
        days: str | int = 0,
        months: str | int = 0,
        years: str | int = 0,
        base_date: str | None = None,
    ) -> DateOutput:
        days_value = self._normalize_offset(days)
        months_value = self._normalize_offset(months)
        years_value = self._normalize_offset(years)
        base = self._parse_date(base_date) if base_date is not None else self._current_datetime()
        base_text = base.strftime("%Y-%m-%d")
        calculated = base + relativedelta(days=days_value, months=months_value, years=years_value)
        parts = [
            self._render_offset_part(years_value, "rok", "lata", "lat"),
            self._render_offset_part(months_value, "miesiąc", "miesiące", "miesięcy"),
            self._render_offset_part(days_value, "dzień", "dni", "dni"),
        ]
        rendered = [part for part in parts if part]
        description = f"{', '.join(rendered)} {base_text}" if rendered else f"Data bazowa: {base_text}"
        return DateOutput(
            base_date=base_text,
            calculated_date=calculated.strftime("%Y-%m-%d"),
            days_offset=days_value,
            months_offset=months_value,
            years_offset=years_value,
            description=description,
        )

    @staticmethod
    def _current_datetime() -> datetime:
        return datetime.now()

    @staticmethod
    def _normalize_offset(value: str | int) -> int:
        if isinstance(value, bool):
            raise ValueError("Nieprawidłowa wartość przesunięcia: wartość logiczna nie jest dozwolona.")
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _invalid_date_error(value: str) -> ValueError:
        return ValueError(
            f"Nieprawidłowy format daty: '{value}'. "
            f"Obsługiwane formaty: YYYY-MM-DD (np. '2024-01-15'), "
            f"YYYY-MM (np. '2024-01', domyślnie 1. dzień), "
            f"YYYY (np. '2024', domyślnie 1 stycznia)."
        )

    @classmethod
    def _parse_date(cls, value: str) -> datetime:
        value = value.strip()
        formats = {
            r"^\d{4}$": "%Y",
            r"^\d{4}-\d{2}$": "%Y-%m",
            r"^\d{4}-\d{2}-\d{2}$": "%Y-%m-%d",
        }
        for pattern, date_format in formats.items():
            if re.fullmatch(pattern, value):
                try:
                    parsed = datetime.strptime(value, date_format)
                    return parsed.replace(day=1) if date_format != "%Y-%m-%d" else parsed
                except ValueError:
                    raise cls._invalid_date_error(value) from None
        raise cls._invalid_date_error(value)

    @staticmethod
    def _quantity(value: int, singular: str, paucal: str, plural: str) -> str:
        amount = abs(value)
        if amount == 0:
            return ""
        if amount == 1:
            return f"1 {singular}"
        last_two = amount % 100
        last_one = amount % 10
        if 12 <= last_two <= 14:
            form = plural
        elif 2 <= last_one <= 4:
            form = paucal
        else:
            form = plural
        return f"{amount} {form}"

    @staticmethod
    def _render_offset_part(value: int, singular: str, paucal: str, plural: str) -> str:
        if value == 0:
            return ""
        quantity = DateService._quantity(value, singular, paucal, plural)
        direction = "po" if value > 0 else "przed"
        return f"{quantity} {direction}"
