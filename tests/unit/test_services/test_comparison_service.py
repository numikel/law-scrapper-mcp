"""Tests for ComparisonService."""

import asyncio
from unittest.mock import AsyncMock

from law_scrapper_mcp.models.tool_outputs import ActDetailOutput
from law_scrapper_mcp.services.comparison_service import ComparisonService


def _details(eli: str, title: str, keywords: list[str]) -> ActDetailOutput:
    publisher, year, pos = eli.split("/")
    return ActDetailOutput(
        eli=eli,
        publisher=publisher,
        year=int(year),
        pos=int(pos),
        title=title,
        status="akt obowiązujący",
        type="Ustawa",
        promulgation_date="2024-01-01",
        entry_into_force="2024-02-01",
        keywords=keywords,
    )


async def test_compare_fetches_both_acts_concurrently() -> None:
    active = 0
    maximum_active = 0

    async def get_details(eli: str, load_content: bool = False) -> ActDetailOutput:
        nonlocal active, maximum_active
        assert load_content is False
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1
        return _details(eli, eli, ["prawo"])

    act_service = AsyncMock()
    act_service.get_details.side_effect = get_details

    result = await ComparisonService(act_service).compare("DU/2024/1", "DU/2024/2")

    assert maximum_active == 2
    assert result.eli_a == "DU/2024/1"
    assert result.eli_b == "DU/2024/2"


async def test_compare_reports_common_keywords_and_differences() -> None:
    act_service = AsyncMock()
    act_service.get_details.side_effect = [
        _details("DU/2024/1", "Pierwsza ustawa", ["prawo", "podatek"]),
        _details("DU/2024/2", "Druga ustawa", ["prawo", "transport"]),
    ]

    result = await ComparisonService(act_service).compare("DU/2024/1", "DU/2024/2")

    assert result.common_keywords == ["prawo"]
    assert "Tytuły różnią się" in result.differences
    assert "Słowa kluczowe tylko w A: podatek" in result.differences
    assert "Słowa kluczowe tylko w B: transport" in result.differences
