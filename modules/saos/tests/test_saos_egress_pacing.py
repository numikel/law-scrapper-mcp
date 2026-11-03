"""The pace SAOS is asked for: below the hub's defaults, registered at start, and kept by the limiter."""

from __future__ import annotations

import pytest
import respx
from mcp import Client
from saos_testing import IDENTITY, FakeSaos, HarnessFactory, loaded

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.egress import EgressDefaults, EgressHost, EgressHub
from law_scrapper_mcp.config import Settings
from law_scrapper_mcp_saos.state import BASE_URL, HOST_LABEL, RATE_BURST, RATE_PER_SECOND, saos_host

pytestmark = pytest.mark.asyncio


async def test_the_module_pace_is_two_requests_per_second_with_a_burst_of_four() -> None:
    host = saos_host(Settings(source_modules=[]))

    assert (host.rate_per_second, host.rate_burst) == (2.0, 4) == (RATE_PER_SECOND, RATE_BURST)
    assert (host.name, host.base_url) == (HOST_LABEL, BASE_URL)


async def test_the_pace_is_never_above_what_the_hub_allows_a_module() -> None:
    defaults = EgressDefaults()
    host = saos_host(Settings(source_modules=[]))

    assert host.rate_per_second is not None and host.rate_per_second <= defaults.rate_per_second
    assert host.rate_burst is not None and host.rate_burst <= defaults.rate_burst


@pytest.mark.parametrize(("rate", "burst"), [(1.0, 2), (0.5, 1), (2.0, 4), (50.0, 100)])
async def test_an_operator_who_slowed_the_sejm_down_does_not_stop_the_module_from_starting(
    rate: float, burst: int
) -> None:
    settings = Settings(source_modules=[], api_rate_per_second=rate, api_rate_burst=burst)
    host = saos_host(settings)
    hub = EgressHub(TTLCache(max_entries=10), EgressDefaults(rate_per_second=rate, rate_burst=burst))

    hub.client_for(host, IDENTITY)  # raises EgressConfigError when the host asks for more than the defaults

    assert host.rate_per_second == min(2.0, rate)
    assert host.rate_burst == min(4, burst)


async def test_the_start_hook_registers_the_host_with_that_pace(
    monkeypatch: pytest.MonkeyPatch, saos_router: respx.MockRouter
) -> None:
    from law_scrapper_mcp.server import build_app

    seen: list[EgressHost] = []
    original = EgressHub.client_for

    def spy(self: EgressHub, host: EgressHost, caller: object) -> object:
        seen.append(host)
        return original(self, host, caller)  # type: ignore[arg-type]

    monkeypatch.setattr(EgressHub, "client_for", spy)
    FakeSaos().install(saos_router)

    async with Client(build_app([loaded()])):
        pass

    ours = [host for host in seen if host.name == HOST_LABEL]
    assert len(ours) == 1
    assert (ours[0].rate_per_second, ours[0].rate_burst) == (2.0, 4)


async def test_the_limiter_keeps_the_pace_after_the_burst(
    make_harness: HarnessFactory, instant_waits: list[float]
) -> None:
    harness = make_harness()

    for page in range(6):
        await harness.service.search(limit=1, offset=page)

    assert harness.fake.pages == [0, 1, 2, 3, 4, 5]
    assert instant_waits == pytest.approx([0.5, 0.5])


async def test_a_search_with_portal_links_pays_for_its_six_requests_in_about_a_second(
    make_harness: HarnessFactory, instant_waits: list[float]
) -> None:
    harness = make_harness()

    await harness.service.search(limit=20, include_portal_links=True)

    assert harness.fake.requests == 6
    assert sum(instant_waits) == pytest.approx(1.0)
