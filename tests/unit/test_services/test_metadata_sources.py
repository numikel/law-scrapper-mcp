"""The `publishers` category with registered act sources: merge, paging, outage of the Sejm, hints."""

from __future__ import annotations

from typing import Any

import pytest
import respx
from httpx import Response

from fake_act_source import FakeActSource
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.models.enums import MetadataCategory
from law_scrapper_mcp.services.act_sources import ActSourceRegistry, PublisherInfo
from law_scrapper_mcp.services.metadata_service import MetadataService
from law_scrapper_mcp.services.response_enrichment import metadata_hints

pytestmark = pytest.mark.asyncio

ACTS = "https://api.sejm.gov.pl/eli/acts"


def described(code: str, label: str, *, available: bool = True) -> PublisherInfo:
    return PublisherInfo(
        code=code, label=label, available=available, item={"code": code, "voivodeship": label, "available": available}
    )


def registry_with(*infos: PublisherInfo) -> ActSourceRegistry:
    registry = ActSourceRegistry()
    registry.register(FakeActSource(), publishers=[info.code for info in infos], info=infos)
    return registry


def service_for(client: SejmApiClient, registry: ActSourceRegistry | None = None) -> MetadataService:
    return MetadataService(client, sources=registry)


def codes(output_publishers: list[Any]) -> list[str]:
    return [entry["code"] for entry in output_publishers]


# --- no described publisher: nothing changes ---------------------------------------------------


@respx.mock
@pytest.mark.parametrize("with_registry", [False, True], ids=["no registry", "empty registry"])
async def test_without_described_publishers_the_list_is_the_sejms_alone(
    mock_client: SejmApiClient, publishers_data: list, with_registry: bool
) -> None:
    respx.get(ACTS).mock(return_value=Response(200, json=publishers_data))
    service = service_for(mock_client, ActSourceRegistry() if with_registry else None)

    output = await service.get_metadata_page(MetadataCategory.PUBLISHERS)

    assert output.metadata["publishers"] == publishers_data
    assert service.has_sources is False


async def test_a_source_that_describes_nothing_does_not_count(mock_client: SejmApiClient) -> None:
    registry = ActSourceRegistry()
    registry.register(FakeActSource(), publishers=["ZZ_1"])

    assert service_for(mock_client, registry).has_sources is False


@respx.mock
async def test_a_failed_sejm_list_without_sources_still_raises_for_the_single_category(
    mock_client: SejmApiClient,
) -> None:
    respx.get(ACTS).mock(return_value=Response(500))

    with pytest.raises(Exception):  # noqa: B017
        await service_for(mock_client, ActSourceRegistry()).get_metadata_page(MetadataCategory.PUBLISHERS)


# --- described publishers: merged after the Sejm's ---------------------------------------------


@respx.mock
async def test_described_publishers_follow_the_sejms_in_code_order(
    mock_client: SejmApiClient, publishers_data: list
) -> None:
    respx.get(ACTS).mock(return_value=Response(200, json=publishers_data))
    service = service_for(mock_client, registry_with(described("ZZ_B", "beta"), described("ZZ_A", "alfa")))

    output = await service.get_metadata_page(MetadataCategory.PUBLISHERS)

    assert codes(output.metadata["publishers"]) == ["DU", "MP", "ZZ_A", "ZZ_B"]
    assert output.metadata["publishers"][2] == {"code": "ZZ_A", "voivodeship": "alfa", "available": True}
    assert output.failed_categories == []
    assert service.has_sources is True


@respx.mock
async def test_the_listing_hands_out_copies_not_the_registered_entries(
    mock_client: SejmApiClient, publishers_data: list
) -> None:
    respx.get(ACTS).mock(return_value=Response(200, json=publishers_data))
    registry = registry_with(described("ZZ_A", "alfa"))
    service = service_for(mock_client, registry)

    first = await service.get_metadata_page(MetadataCategory.PUBLISHERS)
    first.metadata["publishers"][2]["voivodeship"] = "tampered"
    second = await service.get_metadata_page(MetadataCategory.PUBLISHERS)

    assert second.metadata["publishers"][2]["voivodeship"] == "alfa"


@respx.mock
async def test_the_registry_is_read_at_call_time_not_at_construction(
    mock_client: SejmApiClient, publishers_data: list
) -> None:
    respx.get(ACTS).mock(return_value=Response(200, json=publishers_data))
    registry = ActSourceRegistry()
    service = service_for(mock_client, registry)  # built first, like in the server lifespan
    before = await service.get_metadata_page(MetadataCategory.PUBLISHERS)

    info = described("ZZ_A", "alfa")
    registry.register(FakeActSource(), publishers=["ZZ_A"], info=[info])
    after = await service.get_metadata_page(MetadataCategory.PUBLISHERS)

    assert codes(before.metadata["publishers"]) == ["DU", "MP"]
    assert codes(after.metadata["publishers"]) == ["DU", "MP", "ZZ_A"]


@respx.mock
@pytest.mark.parametrize("category", [MetadataCategory.PUBLISHERS, MetadataCategory.ALL])
async def test_the_registry_is_asked_once_per_call(
    mock_client: SejmApiClient, publishers_data: list, monkeypatch: pytest.MonkeyPatch, category: MetadataCategory
) -> None:
    for name, payload in (
        ("keywords", ["prawo"]),
        ("acts", publishers_data),
        ("statuses", ["akt obowiązujący"]),
        ("types", ["Ustawa"]),
        ("institutions", ["Sejm RP"]),
    ):
        respx.get(f"https://api.sejm.gov.pl/eli/{name}").mock(return_value=Response(200, json=payload))
    registry = registry_with(described("ZZ_A", "alfa"))
    asked: list[int] = []
    original = registry.describe

    def counting() -> tuple[PublisherInfo, ...]:
        asked.append(1)
        return original()

    monkeypatch.setattr(registry, "describe", counting)

    await service_for(mock_client, registry).get_metadata_page(category)

    assert len(asked) == 1, "the descriptions are read from the registry once and passed along"


@respx.mock
async def test_paging_and_totals_count_the_described_publishers(
    mock_client: SejmApiClient, publishers_data: list
) -> None:
    respx.get(ACTS).mock(return_value=Response(200, json=publishers_data))
    service = service_for(mock_client, registry_with(described("ZZ_A", "alfa"), described("ZZ_B", "beta")))

    page = await service.get_metadata_page(MetadataCategory.PUBLISHERS, limit=2, offset=1)

    assert page.page_info.total_count == 4
    assert codes(page.metadata["publishers"]) == ["MP", "ZZ_A"]


@respx.mock
async def test_the_all_category_lists_them_too(mock_client: SejmApiClient, publishers_data: list) -> None:
    respx.get("https://api.sejm.gov.pl/eli/keywords").mock(return_value=Response(200, json=["prawo"]))
    respx.get(ACTS).mock(return_value=Response(200, json=publishers_data))
    respx.get("https://api.sejm.gov.pl/eli/statuses").mock(return_value=Response(200, json=["akt obowiązujący"]))
    respx.get("https://api.sejm.gov.pl/eli/types").mock(return_value=Response(200, json=["Ustawa"]))
    respx.get("https://api.sejm.gov.pl/eli/institutions").mock(return_value=Response(200, json=["Sejm RP"]))
    service = service_for(mock_client, registry_with(described("ZZ_A", "alfa")))

    output = await service.get_metadata_page(MetadataCategory.ALL)

    assert codes(output.metadata["publishers"]) == ["DU", "MP", "ZZ_A"]
    assert output.page_info.total_count == 1 + 3 + 1 + 1 + 1


# --- the Sejm is down: the described publishers are still listed -------------------------------


@respx.mock
async def test_a_sejm_outage_keeps_the_described_publishers_in_the_all_category(mock_client: SejmApiClient) -> None:
    respx.get("https://api.sejm.gov.pl/eli/keywords").mock(return_value=Response(200, json=["prawo"]))
    respx.get(ACTS).mock(return_value=Response(500))
    respx.get("https://api.sejm.gov.pl/eli/statuses").mock(return_value=Response(200, json=["akt obowiązujący"]))
    respx.get("https://api.sejm.gov.pl/eli/types").mock(return_value=Response(200, json=["Ustawa"]))
    respx.get("https://api.sejm.gov.pl/eli/institutions").mock(return_value=Response(200, json=["Sejm RP"]))
    service = service_for(mock_client, registry_with(described("ZZ_A", "alfa")))

    output = await service.get_metadata_page(MetadataCategory.ALL)

    assert codes(output.metadata["publishers"]) == ["ZZ_A"]
    assert output.failed_categories == ["publishers"], "a partial list is never presented as complete"


@respx.mock
async def test_a_sejm_outage_keeps_them_for_the_single_category_too(mock_client: SejmApiClient) -> None:
    respx.get(ACTS).mock(return_value=Response(500))
    service = service_for(mock_client, registry_with(described("ZZ_A", "alfa")))

    output = await service.get_metadata_page(MetadataCategory.PUBLISHERS)

    assert codes(output.metadata["publishers"]) == ["ZZ_A"]
    assert output.failed_categories == ["publishers"]


@respx.mock
async def test_another_category_failing_is_still_an_error_for_a_single_category_request(
    mock_client: SejmApiClient,
) -> None:
    respx.get("https://api.sejm.gov.pl/eli/keywords").mock(return_value=Response(500))
    service = service_for(mock_client, registry_with(described("ZZ_A", "alfa")))

    with pytest.raises(Exception):  # noqa: B017
        await service.get_metadata_page(MetadataCategory.KEYWORDS)


# --- the hint ----------------------------------------------------------------------------------


@pytest.mark.parametrize("category", ["all", "publishers"])
async def test_the_hint_appears_only_with_described_publishers(category: str) -> None:
    plain = metadata_hints(category)
    with_sources = metadata_hints(category, publisher_sources=True)

    assert len(with_sources) == len(plain) + 1
    extra = [hint for hint in with_sources if hint not in plain]
    assert len(extra) == 1
    assert "publisher" in extra[0].message and "w ELI zawsze używaj kodu" in extra[0].message
    assert extra[0].tool == "search_legal_acts"


@pytest.mark.parametrize("category", ["keywords", "types", "statuses", "institutions"])
async def test_the_hint_is_not_added_to_other_categories(category: str) -> None:
    assert metadata_hints(category, publisher_sources=True) == metadata_hints(category)


async def test_the_default_hints_are_unchanged() -> None:
    assert [hint.message for hint in metadata_hints("all")] == [
        "Użyj pobranych słów kluczowych do wyszukiwania aktów prawnych.",
        "Filtruj wyniki wyszukiwania po typie dokumentu (np. 'Ustawa', 'Rozporządzenie').",
    ]
    assert metadata_hints("publishers") == []
