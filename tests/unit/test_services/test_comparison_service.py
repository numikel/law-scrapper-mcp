"""Tests for ComparisonService."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from law_scrapper_mcp.models.tool_outputs import ActDetailOutput
from law_scrapper_mcp.services.comparison_service import ComparisonService

pytestmark = pytest.mark.asyncio


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


def _minimal_details(**overrides: object) -> ActDetailOutput:
    defaults: dict[str, object] = {
        "eli": "DU/2024/1",
        "publisher": "DU",
        "year": 2024,
        "pos": 1,
        "title": "Title",
        "status": "akt obowiązujący",
        "type": "Ustawa",
        "announcement_date": None,
        "promulgation_date": "2024-01-01",
        "entry_into_force": "2024-02-01",
        "keywords": [],
    }
    defaults.update(overrides)
    return ActDetailOutput(**defaults)


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


async def test_compare_cancels_sibling_fetch_on_failure() -> None:
    sibling_cancelled = asyncio.Event()
    original_error = RuntimeError("fetch failed")

    async def get_details(eli: str, load_content: bool = False) -> ActDetailOutput:
        assert load_content is False
        if eli == "DU/2024/1":
            raise original_error
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise
        return _details(eli, eli, [])

    act_service = AsyncMock()
    act_service.get_details.side_effect = get_details

    with pytest.raises(RuntimeError, match="fetch failed") as exc_info:
        await ComparisonService(act_service).compare("DU/2024/1", "DU/2024/2")

    assert exc_info.value is original_error
    await asyncio.wait_for(sibling_cancelled.wait(), timeout=1.0)


async def test_compare_cancels_both_fetches_on_parent_cancellation() -> None:
    active_fetches = 0
    both_started = asyncio.Event()
    fetches_cancelled: list[str] = []
    all_cancelled = asyncio.Event()

    async def get_details(eli: str, load_content: bool = False) -> ActDetailOutput:
        nonlocal active_fetches
        assert load_content is False
        active_fetches += 1
        if active_fetches == 2:
            both_started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            fetches_cancelled.append(eli)
            if len(fetches_cancelled) == 2:
                all_cancelled.set()
            raise
        return _details(eli, eli, [])

    act_service = AsyncMock()
    act_service.get_details.side_effect = get_details
    compare_task = asyncio.create_task(
        ComparisonService(act_service).compare("DU/2024/1", "DU/2024/2"),
    )

    await asyncio.wait_for(both_started.wait(), timeout=1.0)
    compare_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await compare_task

    await asyncio.wait_for(all_cancelled.wait(), timeout=1.0)
    assert sorted(fetches_cancelled) == ["DU/2024/1", "DU/2024/2"]


async def test_compare_reuses_single_fetch_for_identical_elis() -> None:
    act_service = AsyncMock()
    act_service.get_details.return_value = _details("DU/2024/1", "Ustawa testowa", ["prawo"])

    result = await ComparisonService(act_service).compare("DU/2024/1", "DU/2024/1")

    act_service.get_details.assert_awaited_once_with(eli="DU/2024/1", load_content=False)
    assert result.eli_a == "DU/2024/1"
    assert result.eli_b == "DU/2024/1"
    assert result.comparison["title_a"] == result.comparison["title_b"] == "Ustawa testowa"
    assert result.common_keywords == ["prawo"]
    assert result.differences == ["Brak istotnych różnic w metadanych"]


async def test_compare_builds_comparison_mapping_with_na_fallbacks() -> None:
    act_service = AsyncMock()
    act_service.get_details.side_effect = [
        _minimal_details(
            eli="DU/2024/1",
            title="A",
            type=None,
            announcement_date=None,
            promulgation_date=None,
            entry_into_force=None,
            keywords=["b", "a"],
        ),
        _minimal_details(
            eli="DU/2024/2",
            publisher="DU",
            year=2024,
            pos=2,
            title="B",
            status="uchylony",
            type="Rozporządzenie",
            announcement_date="2023-06-01",
            promulgation_date="2023-07-01",
            entry_into_force="2023-08-01",
            keywords=["d", "c"],
        ),
    ]

    result = await ComparisonService(act_service).compare("DU/2024/1", "DU/2024/2")

    assert result.comparison == {
        "title_a": "A",
        "title_b": "B",
        "type_a": "N/A",
        "type_b": "Rozporządzenie",
        "status_a": "akt obowiązujący",
        "status_b": "uchylony",
        "announcement_date_a": "N/A",
        "announcement_date_b": "2023-06-01",
        "promulgation_date_a": "N/A",
        "promulgation_date_b": "2023-07-01",
        "entry_into_force_a": "N/A",
        "entry_into_force_b": "2023-08-01",
        "keywords_a": ["b", "a"],
        "keywords_b": ["d", "c"],
    }


async def test_compare_reports_metadata_field_differences() -> None:
    act_service = AsyncMock()
    act_service.get_details.side_effect = [
        _minimal_details(
            eli="DU/2024/1",
            title="A",
            type="Ustawa",
            status="akt obowiązujący",
            promulgation_date="2024-01-01",
            entry_into_force="2024-02-01",
            keywords=["shared"],
        ),
        _minimal_details(
            eli="DU/2024/2",
            publisher="DU",
            year=2024,
            pos=2,
            title="B",
            type="Rozporządzenie",
            status="uchylony",
            promulgation_date="2024-03-01",
            entry_into_force="2024-04-01",
            keywords=["shared"],
        ),
    ]

    result = await ComparisonService(act_service).compare("DU/2024/1", "DU/2024/2")

    assert result.differences == [
        "Tytuły różnią się",
        "Typy różnią się: 'Ustawa' vs 'Rozporządzenie'",
        "Statusy różnią się: 'akt obowiązujący' vs 'uchylony'",
        "Daty promulgacji różnią się: '2024-01-01' vs '2024-03-01'",
        "Daty wejścia w życie różnią się: '2024-02-01' vs '2024-04-01'",
    ]


async def test_compare_sorts_keyword_differences_deterministically() -> None:
    act_service = AsyncMock()
    act_service.get_details.side_effect = [
        _minimal_details(eli="DU/2024/1", keywords=["zebra", "alpha", "shared"]),
        _minimal_details(
            eli="DU/2024/2",
            publisher="DU",
            year=2024,
            pos=2,
            keywords=["beta", "shared"],
        ),
    ]

    result = await ComparisonService(act_service).compare("DU/2024/1", "DU/2024/2")

    assert result.common_keywords == ["shared"]
    assert "Słowa kluczowe tylko w A: alpha, zebra" in result.differences
    assert "Słowa kluczowe tylko w B: beta" in result.differences


async def test_compare_reports_no_metadata_differences() -> None:
    details = _minimal_details(
        title="Same",
        type="Ustawa",
        status="akt obowiązujący",
        promulgation_date="2024-01-01",
        entry_into_force="2024-02-01",
        keywords=["prawo"],
    )
    act_service = AsyncMock()
    act_service.get_details.side_effect = [details, details]

    result = await ComparisonService(act_service).compare("DU/2024/1", "DU/2024/2")

    assert result.common_keywords == ["prawo"]
    assert result.differences == ["Brak istotnych różnic w metadanych"]
