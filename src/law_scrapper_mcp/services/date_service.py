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
        base = self._parse_date(base_date) if base_date else datetime.now()
        base_text = base.strftime("%Y-%m-%d")
        calculated = base + relativedelta(days=days_value, months=months_value, years=years_value)
        parts = [
            self._quantity(years_value, "rok", "lata", "lat"),
            self._quantity(months_value, "miesiąc", "miesiące", "miesięcy"),
            self._quantity(days_value, "dzień", "dni", "dni"),
        ]
        rendered = [part for part in parts if part]
        direction = "po" if days_value + months_value + years_value >= 0 else "przed"
        description = f"{', '.join(rendered)} {direction} {base_text}" if rendered else f"Data bazowa: {base_text}"
        return DateOutput(
            base_date=base_text,
            calculated_date=calculated.strftime("%Y-%m-%d"),
            days_offset=days_value,
            months_offset=months_value,
            years_offset=years_value,
            description=description,
        )

    @staticmethod
    def _normalize_offset(value: str | int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _parse_date(value: str) -> datetime:
        value = value.strip()
        formats = {
            r"^\d{4}$": "%Y",
            r"^\d{4}-\d{2}$": "%Y-%m",
            r"^\d{4}-\d{2}-\d{2}$": "%Y-%m-%d",
        }
        for pattern, date_format in formats.items():
            if re.fullmatch(pattern, value):
                parsed = datetime.strptime(value, date_format)
                return parsed.replace(day=1) if date_format != "%Y-%m-%d" else parsed
        raise ValueError(
            f"Nieprawidłowy format daty: '{value}'. Obsługiwane formaty: YYYY, YYYY-MM, YYYY-MM-DD."
        )

    @staticmethod
    def _quantity(value: int, singular: str, paucal: str, plural: str) -> str:
        amount = abs(value)
        if amount == 0:
            return ""
        if amount == 1:
            return f"1 {singular}"
        if 2 <= amount <= 4:
            return f"{amount} {paucal}"
        return f"{amount} {plural}"
