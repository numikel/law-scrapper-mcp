"""A started module over a real egress hub that talks to no one but `respx`."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from journal_support import CALLER, Started

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.egress import EgressDefaults, EgressHub
from law_scrapper_mcp.config import Settings
from law_scrapper_mcp.services.act_sources import ActSourceRegistry
from law_scrapper_mcp.source_modules.contract import ModuleHost


@pytest_asyncio.fixture
async def started() -> AsyncIterator[Started]:
    """Run the real start hook against a fresh hub and registry; stop it again afterwards."""
    from law_scrapper_mcp_voivodeship_journals import start

    hub = EgressHub(TTLCache(max_entries=100), EgressDefaults(user_agent=Settings().user_agent))
    registry = ActSourceRegistry()
    host = ModuleHost(egress=hub, caller=CALLER, settings=Settings(), act_sources=registry)
    try:
        async with start(host):
            yield Started(hub=hub, registry=registry, host=host)
    finally:
        await hub.close()
