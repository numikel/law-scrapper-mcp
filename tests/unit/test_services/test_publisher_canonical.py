"""DU and MP typed loosely reach the Sejm as DU and MP, but only while a source is registered."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
import respx
from httpx import Response

from fake_act_source import FakeActSource
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.services.act_sources import ActSourceRegistry
from law_scrapper_mcp.services.changes_service import ChangesService
from law_scrapper_mcp.services.result_store import ResultStore
from law_scrapper_mcp.services.search_service import SearchService

pytestmark = pytest.mark.asyncio

SEARCH_URL = "https://api.sejm.gov.pl/eli/acts/search"


def with_a_source() -> ActSourceRegistry:
    registry = ActSourceRegistry()
    registry.register(FakeActSource(), publishers=["ZZ_TEST"])
    return registry


def operations(client: SejmApiClient, registry: ActSourceRegistry | None) -> dict[str, Callable[[str], Awaitable[Any]]]:
    """The three operations that take the publisher as a parameter, each given the typed publisher."""
    kwargs: dict[str, Any] = {} if registry is None else {"sources": registry}
    search = SearchService(client, ResultStore(), **kwargs)
    changes = ChangesService(client, ResultStore(), **kwargs)
    return {
        "search": lambda publisher: search.search(publisher, year=2024),
        "browse": lambda publisher: search.browse(publisher, 2024),
        "track": lambda publisher: changes.track_changes(publisher, "2024-01-01", "2024-12-31"),
    }


@respx.mock
@pytest.mark.parametrize("operation", ["search", "browse", "track"])
@pytest.mark.parametrize(("typed", "sent"), [(" du", "DU"), ("Mp ", "MP"), ("DU", "DU")])
async def test_with_a_source_registered_loose_du_and_mp_reach_the_sejm_canonical(
    mock_client: SejmApiClient, search_results: dict[str, Any], operation: str, typed: str, sent: str
) -> None:
    route = respx.get(SEARCH_URL).mock(return_value=Response(200, json=search_results))

    await operations(mock_client, with_a_source())[operation](typed)

    assert route.call_count == 1
    assert route.calls.last.request.url.params["publisher"] == sent


@respx.mock
@pytest.mark.parametrize("operation", ["search", "browse", "track"])
@pytest.mark.parametrize("registry_kind", ["none", "empty"])
async def test_without_any_source_the_value_goes_to_the_sejm_exactly_as_typed(
    mock_client: SejmApiClient, search_results: dict[str, Any], operation: str, registry_kind: str
) -> None:
    """The 4.4.2 behaviour: a padded or lower-case value is not touched when no module is active."""
    route = respx.get(SEARCH_URL).mock(return_value=Response(200, json=search_results))
    registry = None if registry_kind == "none" else ActSourceRegistry()

    await operations(mock_client, registry)[operation](" du")

    assert route.calls.last.request.url.params["publisher"] == " du"
