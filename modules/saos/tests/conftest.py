"""Fixtures of the SAOS module tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import date

import pytest
import pytest_asyncio
import respx
from saos_testing import FakeSaos, Harness, HarnessFactory, make_saos_client, make_service

from law_scrapper_mcp.client import egress as egress_module
from law_scrapper_mcp.client.egress import EgressHub


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """The egress retry loop waits between attempts; a failing page must not make a test sleep."""

    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(egress_module, "_delay", instant)


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
        return Harness(fake=fake, service=service, store=store, hub=hub)

    yield build
    for hub in hubs:
        await hub.close()
