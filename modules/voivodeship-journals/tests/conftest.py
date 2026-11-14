"""A started module over a real egress hub that talks to no one but `respx`."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from journal_support import CALLER, Started

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.egress import EgressDefaults, EgressHub
from law_scrapper_mcp.config import Settings
from law_scrapper_mcp.services.act_sources import ActSourceRegistry
from law_scrapper_mcp.source_modules.contract import ModuleHost

LOCAL_NAMES = frozenset({"localhost", "127.0.0.1", "::1", "", None})


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make a name lookup for any non-local host fail, so a test that forgot its mock cannot reach a journal.

    `respx` answers below the socket layer and never resolves a name, so a mocked request is
    unaffected; an unmocked one dies here, before a single byte leaves the machine. The journals
    are public institutions and no test may send them traffic.

    A proxy would skip the lookup: the client connects to the proxy, usually on loopback, and the
    proxy resolves the name. `NO_PROXY=*` makes `httpx` ignore every proxy setting, those of the
    environment and those of the operating system, so the request is sent directly and stops here.
    """
    real = socket.getaddrinfo

    def guarded(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host in LOCAL_NAMES:
            return real(host, *args, **kwargs)
        raise RuntimeError(f"Test tried to resolve {host!r}: the module tests never reach the network.")

    monkeypatch.setattr(socket, "getaddrinfo", guarded)
    for variable in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(variable, "*")


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
