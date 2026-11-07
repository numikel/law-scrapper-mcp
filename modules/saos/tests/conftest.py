"""Fixtures of the SAOS module tests."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import date
from typing import Any

import pytest
import pytest_asyncio
import respx
from saos_reading_testing import FakeReading, ReadingHarness, make_reading_harness, pair_fake
from saos_testing import CLOCK, FakeSaos, Harness, HarnessFactory, make_saos_client, make_service

from law_scrapper_mcp.client import egress as egress_module
from law_scrapper_mcp.client import rate_limiter as limiter_module
from law_scrapper_mcp.client.egress import EgressHub

LOCAL_NAMES = frozenset({"localhost", "127.0.0.1", "::1", "", None})


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make a name lookup for any non-local host fail, so a test that forgot its mock cannot reach SAOS.

    `respx` answers below the socket layer and never resolves a name, so a mocked request is unaffected;
    an unmocked one dies here, before a single byte leaves the machine. SAOS is an academic service and
    no test may send it traffic. The guard covers lookups by name, which is how every request of this
    module is addressed; a request to a bare IP literal would skip the lookup, and the module builds none.
    """
    real = socket.getaddrinfo

    def guarded(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host in LOCAL_NAMES:
            return real(host, *args, **kwargs)
        raise RuntimeError(f"Test tried to resolve {host!r}: the module tests never reach the network.")

    monkeypatch.setattr(socket, "getaddrinfo", guarded)


@pytest.fixture(autouse=True)
def instant_waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Make both waiting points of the egress client free, and record the pacing waits.

    The retry loop waits between attempts and the limiter waits for a token; neither may make a test sleep.
    A limiter wait advances the shared fake clock, so the limiter still behaves as if the time had passed.
    The returned list holds every limiter wait, in seconds.
    """
    waited: list[float] = []

    async def retry_delay(_seconds: float) -> None:
        return None

    async def limiter_wait(seconds: float) -> None:
        waited.append(seconds)
        CLOCK.now += seconds
        await asyncio.sleep(0)

    monkeypatch.setattr(egress_module, "_delay", retry_delay)
    monkeypatch.setattr(limiter_module, "_wait", limiter_wait)
    return waited


@pytest.fixture
def saos_router() -> Iterator[respx.MockRouter]:
    """A mock router that fails on any request nobody registered a route for."""
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest_asyncio.fixture
async def make_harness(saos_router: respx.MockRouter) -> AsyncIterator[HarnessFactory]:
    """Factory of harnesses; every hub it built is closed after the test."""
    hubs: list[EgressHub] = []

    def build(
        fake: FakeSaos | None = None,
        *,
        today: date | None = None,
        search_ttl: int = 600,
        filter_max_records: int | None = None,
    ) -> Harness:
        fake = (fake or FakeSaos()).install(saos_router)
        client, hub = make_saos_client(search_ttl=search_ttl)
        hubs.append(hub)
        service, store = make_service(
            client, today=(lambda: today) if today else None, filter_max_records=filter_max_records
        )
        return Harness(fake=fake, service=service, store=store, hub=hub, client=client)

    yield build
    for hub in hubs:
        await hub.close()


@pytest_asyncio.fixture
async def make_reading(saos_router: respx.MockRouter) -> AsyncIterator[Callable[[FakeReading], ReadingHarness]]:
    """Factory of reading harnesses (a fake SAOS with the recorded skeletons, the loader, a document store)."""
    harnesses: list[ReadingHarness] = []

    def build(fake: FakeReading | None = None) -> ReadingHarness:
        harness = make_reading_harness(saos_router, fake or pair_fake())
        harnesses.append(harness)
        return harness

    yield build
    for harness in harnesses:
        await harness.hub.close()
