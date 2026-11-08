"""The `coverage` metadata category: its shape alone and inside `all`, failure isolation, hints, paging."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
import respx
from httpx import Response

from fake_coverage_provider import MEASURED_ON, Clock, FakeCoverageProvider, segment
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.models.enums import MetadataCategory
from law_scrapper_mcp.services.coverage import CoverageRegistry
from law_scrapper_mcp.services.metadata_service import MetadataService
from law_scrapper_mcp.services.response_enrichment import metadata_hints

pytestmark = pytest.mark.asyncio

EXPECTED_KEYS = {"source", "segment", "label", "newest", "newest_ref", "count", "years", "measured_on", "origin"}


def _mock_sejm(publishers: list[Any]) -> None:
    for name, payload in (
        ("keywords", ["prawo"]),
        ("acts", publishers),
        ("statuses", ["akt obowiązujący"]),
        ("types", ["Ustawa"]),
        ("institutions", ["Sejm RP"]),
    ):
        respx.get(f"https://api.sejm.gov.pl/eli/{name}").mock(return_value=Response(200, json=payload))


def _registry(*providers: FakeCoverageProvider) -> CoverageRegistry:
    clock = Clock()
    registry = CoverageRegistry(today=clock.today, monotonic=clock.monotonic)
    for provider in providers:
        registry.register(provider)
    return registry


async def test_the_category_is_part_of_the_all_order_after_the_sejm_ones() -> None:
    assert MetadataCategory.COVERAGE.value == "coverage"
    assert MetadataService.METADATA_ORDER[-1] is MetadataCategory.COVERAGE
    assert MetadataService.METADATA_ORDER[:-1] == (
        MetadataCategory.KEYWORDS,
        MetadataCategory.PUBLISHERS,
        MetadataCategory.STATUSES,
        MetadataCategory.TYPES,
        MetadataCategory.INSTITUTIONS,
    )


async def test_the_category_flattens_every_segment_of_every_provider(mock_client: SejmApiClient) -> None:
    sejm = FakeCoverageProvider(
        "sejm",
        source="sejm",
        segments=(
            segment("DU", newest=date(2026, 11, 6), newest_ref="2026/5811", count=5000, years=(2024, 2025, 2026)),
            segment("MP", label="Monitor Polski", newest=None, newest_ref=None, count=None, years=None),
        ),
    )
    journals = FakeCoverageProvider("journals", source="voivodeship-journals", segments=(segment("POL_WOJ_PM"),))
    service = MetadataService(mock_client, coverage=_registry(sejm, journals))

    output = await service.get_metadata_page(MetadataCategory.COVERAGE)

    items = output.metadata["coverage"]
    assert list(output.metadata) == ["coverage"]
    assert [(item["source"], item["segment"]) for item in items] == [
        ("voivodeship-journals", "POL_WOJ_PM"),
        ("sejm", "DU"),
        ("sejm", "MP"),
    ]
    assert all(set(item) == EXPECTED_KEYS for item in items)
    du = items[1]
    assert du == {
        "source": "sejm",
        "segment": "DU",
        "label": "Dziennik Ustaw",
        "newest": "2026-11-06",
        "newest_ref": "2026/5811",
        "count": 5000,
        "years": [2024, 2025, 2026],
        "measured_on": MEASURED_ON.isoformat(),
        "origin": "measured",
    }
    mp = items[2]
    assert (mp["newest"], mp["newest_ref"], mp["count"], mp["years"]) == (None, None, None, None)
    assert output.failed_categories == []
    assert output.page_info.total_count == 3


async def test_without_a_registry_or_providers_the_category_is_empty(mock_client: SejmApiClient) -> None:
    for registry in (None, CoverageRegistry()):
        service = MetadataService(mock_client, coverage=registry)

        output = await service.get_metadata_page(MetadataCategory.COVERAGE)

        assert output.metadata == {"coverage": []}
        assert output.page_info.total_count == 0


async def test_a_failed_measurement_is_listed_as_a_fallback(mock_client: SejmApiClient) -> None:
    service = MetadataService(mock_client, coverage=_registry(FakeCoverageProvider("DU", fail_measure=True)))

    output = await service.get_metadata_page(MetadataCategory.COVERAGE)

    assert [item["origin"] for item in output.metadata["coverage"]] == ["fallback"]
    assert output.failed_categories == []


async def test_a_provider_whose_fallback_fails_too_is_skipped_not_fatal(mock_client: SejmApiClient) -> None:
    broken = FakeCoverageProvider("MP", fail_measure=True, fail_fallback=True)
    service = MetadataService(mock_client, coverage=_registry(FakeCoverageProvider("DU"), broken))

    output = await service.get_metadata_page(MetadataCategory.COVERAGE)

    assert [item["segment"] for item in output.metadata["coverage"]] == ["DU"]
    assert output.failed_categories == []


async def test_the_category_pages_like_the_others(mock_client: SejmApiClient) -> None:
    providers = [FakeCoverageProvider(key) for key in ("A", "B", "C")]
    service = MetadataService(mock_client, coverage=_registry(*providers))

    page = await service.get_metadata_page(MetadataCategory.COVERAGE, limit=2, offset=1)

    assert page.page_info.total_count == 3
    assert [item["segment"] for item in page.metadata["coverage"]] == ["B", "C"]


@respx.mock
async def test_all_includes_the_coverage_items_in_the_total(mock_client: SejmApiClient, publishers_data: list) -> None:
    _mock_sejm(publishers_data)
    service = MetadataService(mock_client, coverage=_registry(FakeCoverageProvider("DU")))

    output = await service.get_metadata_page(MetadataCategory.ALL)

    assert list(output.metadata) == ["keywords", "publishers", "statuses", "types", "institutions", "coverage"]
    assert len(output.metadata["coverage"]) == 1
    assert output.page_info.total_count == 1 + len(publishers_data) + 1 + 1 + 1 + 1


@respx.mock
async def test_all_without_providers_keeps_the_sejm_totals_and_an_empty_coverage(
    mock_client: SejmApiClient, publishers_data: list
) -> None:
    _mock_sejm(publishers_data)
    service = MetadataService(mock_client, coverage=CoverageRegistry())

    output = await service.get_metadata_page(MetadataCategory.ALL)

    assert output.metadata["coverage"] == []
    assert output.page_info.total_count == 1 + len(publishers_data) + 1 + 1 + 1


@respx.mock
async def test_a_second_all_in_the_same_day_does_not_measure_again(
    mock_client: SejmApiClient, publishers_data: list
) -> None:
    _mock_sejm(publishers_data)
    provider = FakeCoverageProvider("DU")
    service = MetadataService(mock_client, coverage=_registry(provider))

    await service.get_metadata_page(MetadataCategory.ALL)
    await service.get_metadata_page(MetadataCategory.ALL)
    await service.get_metadata_page(MetadataCategory.COVERAGE)

    assert len(provider.measure_calls) == 1


# --- the hint ----------------------------------------------------------------------------------


@pytest.mark.parametrize("category", ["coverage", "all"])
async def test_the_fallback_hint_appears_only_when_a_fallback_was_listed(category: str) -> None:
    plain = metadata_hints(category)
    with_fallback = metadata_hints(category, coverage_fallback=True)

    extra = [hint for hint in with_fallback if hint not in plain]
    assert len(with_fallback) == len(plain) + 1
    assert len(extra) == 1
    assert 'origin="fallback"' in extra[0].message and "measured_on" in extra[0].message
    assert extra[0].tool == "get_system_metadata"


@pytest.mark.parametrize("category", ["keywords", "types", "statuses", "institutions", "publishers"])
async def test_the_fallback_hint_is_not_added_to_other_categories(category: str) -> None:
    assert metadata_hints(category, coverage_fallback=True) == metadata_hints(category)


async def test_the_coverage_category_has_no_other_hints_by_default() -> None:
    assert metadata_hints("coverage") == []
