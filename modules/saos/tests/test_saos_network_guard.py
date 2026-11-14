"""The autouse guard in `conftest.py` keeps every test of this module off the network.

A guard that was never seen blocking something proves nothing, so each case here is a positive control:
a public name must be refused, and a loopback name must still work (the in-process servers of the other
tests depend on it).
"""

from __future__ import annotations

import asyncio
import socket

import pytest
from saos_testing import make_saos_client

SAOS_HOST = "www.saos.org.pl"
PROXY_VARIABLES = ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")


def _causes(error: BaseException) -> list[str]:
    """The error and everything it was raised from, outermost first."""
    chain = [error]
    while chain[-1].__cause__ or chain[-1].__context__:
        chain.append(chain[-1].__cause__ or chain[-1].__context__)  # type: ignore[arg-type]
    return [repr(link) for link in chain]


def _stopped_at_the_name_lookup(error: BaseException) -> bool:
    return any("never reach the network" in link for link in _causes(error))


def test_a_lookup_of_the_saos_host_is_refused() -> None:
    with pytest.raises(RuntimeError, match="never reach the network"):
        socket.getaddrinfo(SAOS_HOST, 443)


@pytest.mark.parametrize("host", ["example.com", "api.sejm.gov.pl", "8.8.8.8"])
def test_a_lookup_of_any_other_public_host_is_refused(host: str) -> None:
    with pytest.raises(RuntimeError, match="never reach the network"):
        socket.getaddrinfo(host, 443)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_a_loopback_lookup_still_works(host: str) -> None:
    assert socket.getaddrinfo(host, 80)


@pytest.mark.asyncio
async def test_the_module_client_cannot_reach_the_saos_host_without_a_mock() -> None:
    """The whole stack, no `respx` router: the request must die at the name lookup, before any connection."""
    client, hub = make_saos_client()
    try:
        with pytest.raises(Exception) as excinfo:
            await client.search({"pageSize": 1, "pageNumber": 0})
    finally:
        await hub.close()

    assert _stopped_at_the_name_lookup(excinfo.value), _causes(excinfo.value)


@pytest.mark.asyncio
async def test_a_configured_proxy_is_not_a_way_around_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """A proxy resolves the name itself, so a request handed to one never reaches the lookup the guard refuses.

    Developer machines and build sandboxes often export `HTTPS_PROXY`. The proxy here is a server of this
    process, so the test can see whether anything was sent to it: nothing may be, and the request must still
    die at the name lookup.
    """
    reached: list[bool] = []

    async def proxy(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        reached.append(True)
        writer.close()

    server = await asyncio.start_server(proxy, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    for variable in PROXY_VARIABLES:
        monkeypatch.setenv(variable, f"http://127.0.0.1:{port}")
    client, hub = make_saos_client()
    try:
        with pytest.raises(Exception) as excinfo:
            await client.search({"pageSize": 1, "pageNumber": 0})
    finally:
        await hub.close()
        server.close()
        await server.wait_closed()

    assert not reached, "the request was handed to the proxy"
    assert _stopped_at_the_name_lookup(excinfo.value), _causes(excinfo.value)


@pytest.mark.asyncio
async def test_a_loopback_connection_still_works() -> None:
    """Positive control for the other direction: a server of this process is reachable by its name."""
    accepted: list[bool] = []

    async def serve(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        accepted.append(True)
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        _reader, writer = await asyncio.open_connection("localhost", port)
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.05)
    finally:
        server.close()
        await server.wait_closed()

    assert accepted
