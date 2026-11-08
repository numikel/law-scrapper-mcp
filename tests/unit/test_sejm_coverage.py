"""The DU and MP coverage providers on the 2026-11-08 recordings (`tests/fixtures/sejm/`).

Pinned here: what a measurement reads and how many requests it costs, that every request goes through the
one injected `SejmApiClient` (never a second HTTP client), and that the fallback needs no file at runtime.
"""

from __future__ import annotations

import ast
import builtins
import inspect
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.exceptions import ActNotFoundError, SejmApiError
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.services import sejm_coverage
from law_scrapper_mcp.services.coverage import CoverageConflictError, CoverageRegistry
from law_scrapper_mcp.services.sejm_coverage import register_sejm_coverage

SEJM = Path(__file__).parents[1] / "fixtures" / "sejm"
ACTS_URL = "https://api.sejm.gov.pl/eli/acts"
SEARCH_URL = "https://api.sejm.gov.pl/eli/acts/search"
TODAY = date(2026, 11, 9)


def load(name: str) -> Any:
    return json.loads((SEJM / name).read_text(encoding="utf-8"))


class SpyClient(SejmApiClient):
    """The real client that notes every request it puts on the wire (`_request` is the one HTTP funnel)."""

    def __init__(self, cache: TTLCache) -> None:
        super().__init__(cache=cache)
        self.wire: list[str] = []

    async def _request(self, method: str, path: str, *args: Any, **kwargs: Any) -> httpx.Response:
        self.wire.append(path)
        return await super()._request(method, path, *args, **kwargs)


@pytest_asyncio.fixture
async def spy() -> AsyncIterator[SpyClient]:
    client = SpyClient(TTLCache(max_entries=100))
    await client.start()
    yield client
    await client.close()


@contextmanager
def recorded_sejm() -> Iterator[respx.MockRouter]:
    """The Sejm as recorded: the publisher list and the newest-year page of each publisher."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        router.get(ACTS_URL).mock(return_value=httpx.Response(200, json=load("publishers_live.json")))
        for code, name in (("DU", "du"), ("MP", "mp")):
            router.get(SEARCH_URL, params={"publisher": code, "year": "2026", "limit": "1"}).mock(
                return_value=httpx.Response(200, json=load(f"search_2026_limit1_{name}.json"))
            )
        yield router


def registry_for(client: SejmApiClient) -> CoverageRegistry:
    registry = CoverageRegistry(today=lambda: TODAY)
    register_sejm_coverage(registry, client)
    return registry


@pytest.mark.asyncio
async def test_a_measurement_reads_years_count_and_the_newest_act_of_each_publisher(spy: SpyClient) -> None:
    publishers = {entry["code"]: entry for entry in load("publishers_live.json")}
    with recorded_sejm():
        measured = {coverage.segments[0].key: coverage for coverage in await registry_for(spy).all()}

    assert set(measured) == {"DU", "MP"}
    for code, label, ref in (("DU", "Dziennik Ustaw", "2026/1465"), ("MP", "Monitor Polski", "2026/1100")):
        coverage = measured[code]
        segment = coverage.segments[0]
        assert (coverage.source, coverage.origin, coverage.measured_on) == ("sejm", "measured", TODAY)
        assert segment.label == label
        assert segment.newest == date(2026, 11, 5), "the publication date is the item's `promulgation`"
        assert segment.newest_ref == ref
        assert segment.count == publishers[code]["actsCount"]
        assert segment.years == tuple(sorted(publishers[code]["years"]))


@pytest.mark.asyncio
async def test_two_publishers_cost_three_requests_and_a_day_of_answers_cost_no_more(spy: SpyClient) -> None:
    registry = registry_for(spy)
    with recorded_sejm() as router:
        await registry.all()
        await registry.all()
        await registry.get("DU")
        sent = router.calls.call_count

    assert sorted(spy.wire) == ["acts", "acts/search", "acts/search"], "the publisher list is read once for both"
    assert sent == 3


@pytest.mark.asyncio
async def test_every_request_of_a_measurement_goes_through_the_injected_client(spy: SpyClient) -> None:
    """A provider with a client of its own would put requests on the wire that the spy never saw."""
    with recorded_sejm() as router:
        await registry_for(spy).all()
        sent = router.calls.call_count

    assert sent > 0
    assert len(spy.wire) == sent


def test_the_provider_module_builds_no_http_client_of_its_own() -> None:
    source = inspect.getsource(sejm_coverage)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {(node.module or "").split(".")[0] for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)}

    assert "httpx" not in imported
    assert "AsyncClient" not in source


def register_providers_on_scratch_registry(client: SejmApiClient) -> tuple[Any, ...]:
    return register_sejm_coverage(CoverageRegistry(), client)


@pytest.mark.asyncio
async def test_the_fallback_is_made_of_constants_and_reads_no_file(
    spy: SpyClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    providers = register_providers_on_scratch_registry(spy)

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the fallback must not read a file")

    with monkeypatch.context() as patched:
        patched.setattr(builtins, "open", refuse)
        patched.setattr(Path, "read_text", refuse)
        patched.setattr(Path, "open", refuse)
        fallbacks = {provider.key: provider.fallback() for provider in providers}

    assert set(fallbacks) == {"DU", "MP"}
    for key, ref in (("DU", "2026/1465"), ("MP", "2026/1100")):
        coverage = fallbacks[key]
        segment = coverage.segments[0]
        assert coverage.origin == "fallback"
        assert coverage.measured_on == date(2026, 11, 8)
        assert (segment.newest, segment.newest_ref) == (date(2026, 11, 5), ref)
        assert segment.years is None, "a fallback never invents a year gap"


@pytest.mark.asyncio
async def test_the_fallback_constants_equal_the_recordings(spy: SpyClient) -> None:
    publishers = {entry["code"]: entry for entry in load("publishers_live.json")}
    for provider in register_providers_on_scratch_registry(spy):
        segment = provider.fallback().segments[0]
        recorded = load(f"search_2026_limit1_{provider.key.lower()}.json")["items"][0]

        assert segment.newest == date.fromisoformat(recorded["promulgation"])
        assert segment.newest_ref == f"{recorded['year']}/{recorded['pos']}"
        assert segment.count == publishers[provider.key]["actsCount"]


@pytest.mark.asyncio
async def test_a_failed_measurement_is_answered_with_the_fallback(spy: SpyClient) -> None:
    registry = registry_for(spy)
    with respx.mock(assert_all_mocked=True) as router:
        router.get(ACTS_URL).mock(return_value=httpx.Response(404))
        coverage = await registry.get("DU")

    assert coverage is not None
    assert coverage.origin == "fallback"
    assert coverage.segments[0].newest_ref == "2026/1465"


@pytest.mark.asyncio
async def test_a_measurement_raises_when_the_publisher_is_missing_from_the_list(spy: SpyClient) -> None:
    provider = register_providers_on_scratch_registry(spy)[0]
    only_mp = [entry for entry in load("publishers_live.json") if entry["code"] == "MP"]
    with respx.mock(assert_all_mocked=True) as router:
        router.get(ACTS_URL).mock(return_value=httpx.Response(200, json=only_mp))
        with pytest.raises(SejmApiError):
            await provider.measure(TODAY)


@pytest.mark.asyncio
async def test_an_empty_newest_year_keeps_the_years_and_leaves_the_newest_unknown(spy: SpyClient) -> None:
    provider = register_providers_on_scratch_registry(spy)[0]
    with respx.mock(assert_all_mocked=True) as router:
        router.get(ACTS_URL).mock(return_value=httpx.Response(200, json=load("publishers_live.json")))
        router.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"count": 0, "items": [], "totalCount": 0}))
        coverage = await provider.measure(TODAY)

    segment = coverage.segments[0]
    assert (segment.newest, segment.newest_ref) == (None, None)
    assert segment.years is not None and segment.years[-1] == 2026


@pytest.mark.asyncio
async def test_a_failing_host_makes_measure_raise_so_the_registry_can_fall_back(spy: SpyClient) -> None:
    provider = register_providers_on_scratch_registry(spy)[0]
    with respx.mock(assert_all_mocked=True) as router:
        router.get(ACTS_URL).mock(return_value=httpx.Response(404))
        with pytest.raises(ActNotFoundError):
            await provider.measure(TODAY)


@pytest.mark.asyncio
async def test_a_third_provider_cannot_take_the_key_of_du(spy: SpyClient) -> None:
    registry = registry_for(spy)
    other = register_providers_on_scratch_registry(spy)[0]

    with pytest.raises(CoverageConflictError):
        registry.register(other)
