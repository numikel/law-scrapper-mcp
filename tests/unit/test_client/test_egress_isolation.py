"""What keeps one upstream host from touching another's controls.

Phase 2 covers what a single client enforces on its own: the cache namespace, the origin pin
on redirects, the host label on every error. The two-host scenarios built on the hub join them
in the same file.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
import respx

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.circuit_breaker import CircuitState
from law_scrapper_mcp.client.egress import (
    CallerIdentity,
    EgressClient,
    EgressDefaults,
    EgressHost,
    EgressHub,
    SourceState,
)
from law_scrapper_mcp.client.exceptions import (
    ActNotFoundError,
    ApiUnavailableError,
    EgressRedirectRefusedError,
    SejmApiError,
)
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from test_client.conftest import FakeClock

HOST_A = "https://a.example.test/api"
HOST_B = "https://b.example.test/api"


def make_client(base_url: str, name: str, cache: TTLCache | None = None, **host_kwargs: object) -> EgressClient:
    """One attempt per operation, so the first failure is the one that surfaces."""
    return EgressClient(
        host=EgressHost(name=name, base_url=base_url, **host_kwargs),  # type: ignore[arg-type]
        cache=cache if cache is not None else TTLCache(max_entries=50),
        max_attempts=1,
    )


@pytest_asyncio.fixture
async def pinned() -> AsyncGenerator[EgressClient]:
    """A host built with the defaults: origin pinned, three redirect hops."""
    client = make_client(HOST_A, "SAOS")
    await client.start()
    yield client
    await client.close()


# --- cache namespace ---------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_cache_gives_two_hosts_two_entries_for_the_same_path_and_params() -> None:
    cache = TTLCache(max_entries=50)
    route_a = respx.get(f"{HOST_A}/items").mock(return_value=httpx.Response(200, json={"from": "a"}))
    route_b = respx.get(f"{HOST_B}/items").mock(return_value=httpx.Response(200, json={"from": "b"}))
    client_a = make_client(HOST_A, "A", cache)
    client_b = make_client(HOST_B, "B", cache)
    try:
        assert await client_a.get_json("items", {"q": "x"}, cache_ttl=60) == {"from": "a"}
        assert await client_b.get_json("items", {"q": "x"}, cache_ttl=60) == {"from": "b"}
        # Each host's second call is a hit on its own entry, not on the other host's.
        assert await client_a.get_json("items", {"q": "x"}, cache_ttl=60) == {"from": "a"}
        assert await client_b.get_json("items", {"q": "x"}, cache_ttl=60) == {"from": "b"}
    finally:
        await client_a.close()
        await client_b.close()

    assert route_a.call_count == 1
    assert route_b.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_cache_key_is_prefixed_with_the_lower_cased_origin() -> None:
    cache = TTLCache(max_entries=50)
    respx.get("https://Mixed.Example.test:8443/api/items").mock(return_value=httpx.Response(200, json=[1]))
    client = make_client("https://Mixed.Example.test:8443/api", "M", cache)
    try:
        await client.get_json("items", cache_ttl=60)
    finally:
        await client.close()

    assert await cache.get("https://mixed.example.test:8443|json:items:{}", max_age=60) == [1]


# --- origin pin and redirect cap ---------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        pytest.param("https://elsewhere.example.test/api/items", id="other-host"),
        pytest.param("http://a.example.test/api/items", id="scheme-downgrade"),
        pytest.param("https://a.example.test:8443/api/items", id="port-change"),
    ],
)
@respx.mock
async def test_pinned_redirect_to_another_origin_is_refused_before_it_leaves(pinned: EgressClient, target: str) -> None:
    respx.get(f"{HOST_A}/items").mock(return_value=httpx.Response(302, headers={"Location": target}))
    elsewhere = respx.get(target).mock(return_value=httpx.Response(200, json={"leaked": True}))

    with pytest.raises(EgressRedirectRefusedError) as excinfo:
        await pinned.get_json("items", {"secret": "query"})

    assert not elsewhere.called
    assert excinfo.value.source == "SAOS"
    assert not isinstance(excinfo.value, ApiUnavailableError)
    assert str(excinfo.value) == "Źródło SAOS przekierowało żądanie poza własny adres, więc go nie wykonano"


@pytest.mark.asyncio
@respx.mock
async def test_pinned_redirect_is_neither_retried_nor_booked_as_a_breaker_failure() -> None:
    client = EgressClient(
        host=EgressHost(name="SAOS", base_url=HOST_A),
        cache=TTLCache(max_entries=10),
        max_attempts=3,
    )
    first = respx.get(f"{HOST_A}/items").mock(
        return_value=httpx.Response(302, headers={"Location": "https://elsewhere.example.test/x"})
    )
    try:
        with pytest.raises(EgressRedirectRefusedError):
            await client.get_json("items")
    finally:
        await client.close()

    assert first.call_count == 1
    assert client._circuit_breaker.failure_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_pinned_same_origin_redirect_is_followed(pinned: EgressClient) -> None:
    respx.get(f"{HOST_A}/old").mock(return_value=httpx.Response(301, headers={"Location": f"{HOST_A}/new"}))
    respx.get(f"{HOST_A}/new").mock(return_value=httpx.Response(200, json={"ok": True}))

    assert await pinned.get_json("old") == {"ok": True}


@pytest.mark.asyncio
@respx.mock
async def test_pinned_redirect_chain_stops_after_three_hops(pinned: EgressClient) -> None:
    for step in range(5):
        respx.get(f"{HOST_A}/hop{step}").mock(
            return_value=httpx.Response(302, headers={"Location": f"{HOST_A}/hop{step + 1}"})
        )

    with pytest.raises(SejmApiError) as excinfo:
        await pinned.get_json("hop0")

    assert isinstance(excinfo.value.__cause__, httpx.TooManyRedirects)
    assert excinfo.value.source == "SAOS"
    # The original request plus three followed hops; the fourth redirect is the one refused.
    assert sum(route.call_count for route in respx.routes) == 4


@pytest.mark.asyncio
@respx.mock
async def test_sejm_redirect_to_another_host_is_still_followed() -> None:
    """Characterisation of the accepted residual risk: the Sejm client neither pins nor caps at 3.

    Documented in the plan (owner decision Q-2); this test makes a future change deliberate.
    """
    client = SejmApiClient(cache=TTLCache(max_entries=10), max_attempts=1)
    respx.get(f"{SejmApiClient.BASE_URL}/acts/DU/2024/1").mock(
        return_value=httpx.Response(302, headers={"Location": "https://elsewhere.example.test/act"})
    )
    elsewhere = respx.get("https://elsewhere.example.test/act").mock(
        return_value=httpx.Response(200, json={"followed": True})
    )
    try:
        assert await client.get_act("DU", 2024, 1) == {"followed": True}
        assert client._client is not None
        assert client._client.event_hooks["request"] == []
        assert client._client.max_redirects == 20
    finally:
        await client.close()

    assert elsewhere.call_count == 1


# --- host label on errors ----------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_source_is_the_host_name_on_every_error_a_client_raises(pinned: EgressClient) -> None:
    respx.get(f"{HOST_A}/missing").mock(return_value=httpx.Response(404))
    respx.get(f"{HOST_A}/down").mock(return_value=httpx.Response(503))
    respx.get(f"{HOST_A}/gone").mock(side_effect=httpx.ConnectError("refused"))
    respx.get(f"{HOST_A}/teapot").mock(return_value=httpx.Response(418, text="tea"))

    with pytest.raises(ActNotFoundError) as not_found:
        await pinned.get_json("missing")
    with pytest.raises(ApiUnavailableError) as down:
        await pinned.get_json("down")
    with pytest.raises(ApiUnavailableError) as gone:
        await pinned.get_json("gone")
    with pytest.raises(SejmApiError) as teapot:
        await pinned.get_json("teapot")

    assert {exc.value.source for exc in (not_found, down, gone, teapot)} == {"SAOS"}


@pytest.mark.asyncio
@respx.mock
async def test_source_on_an_open_breaker_refusal(pinned: EgressClient) -> None:
    respx.get(f"{HOST_A}/down").mock(return_value=httpx.Response(503))
    for _ in range(5):
        with pytest.raises(ApiUnavailableError):
            await pinned.get_json("down")

    with pytest.raises(ApiUnavailableError) as excinfo:
        await pinned.get_json("down")

    assert excinfo.value.source == "SAOS"
    assert str(excinfo.value) == "Źródło SAOS jest tymczasowo niedostępne (bezpiecznik otwarty)"


@pytest.mark.asyncio
@respx.mock
async def test_source_of_the_sejm_client_is_its_host_name() -> None:
    client = SejmApiClient(cache=TTLCache(max_entries=10), max_attempts=1)
    respx.get(f"{SejmApiClient.BASE_URL}/acts/DU/2024/1").mock(return_value=httpx.Response(503))
    try:
        with pytest.raises(ApiUnavailableError) as excinfo:
            await client.get_act("DU", 2024, 1)
    finally:
        await client.close()

    assert excinfo.value.source == "api.sejm.gov.pl"


@pytest.mark.asyncio
@respx.mock
async def test_a_breaker_trip_log_line_names_the_host_but_the_sejm_stays_unlabelled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    respx.get(f"{HOST_A}/down").mock(return_value=httpx.Response(503))
    respx.get(f"{SejmApiClient.BASE_URL}/acts/DU/2024/1").mock(return_value=httpx.Response(503))
    module_client = make_client(HOST_A, "SAOS")
    sejm_client = SejmApiClient(cache=TTLCache(max_entries=10), max_attempts=1)
    try:
        with caplog.at_level("WARNING", logger="law_scrapper_mcp.client.circuit_breaker"):
            for _ in range(5):
                with pytest.raises(ApiUnavailableError):
                    await module_client.get_json("down")
            module_lines = [r.getMessage() for r in caplog.records]
            caplog.clear()
            for _ in range(5):
                with pytest.raises(ApiUnavailableError):
                    await sejm_client.get_act("DU", 2024, 1)
            sejm_lines = [r.getMessage() for r in caplog.records]
    finally:
        await module_client.close()
        await sejm_client.close()

    assert module_lines == ["Circuit breaker [SAOS] OPENED after 5 failures (threshold: 5)"]
    assert sejm_lines == ["Circuit breaker OPENED after 5 failures (threshold: 5)"]


# --- two hosts through the hub: nothing one does reaches the other ----------------------------

SAOS_URL = "https://saos.example.test/api"
OTHER_URL = "https://other.example.test/api"


def two_host_hub(clock: FakeClock, **overrides: object) -> tuple[EgressHub, EgressClient, EgressClient]:
    """A hub on the fake clock, one attempt per operation, with a client for each of two hosts."""
    defaults = EgressDefaults(max_attempts=1, user_agent="law-scrapper-mcp/test", **overrides)  # type: ignore[arg-type]
    hub = EgressHub(TTLCache(max_entries=50), defaults, clock=clock)
    saos = hub.client_for(EgressHost(name="SAOS", base_url=SAOS_URL), CallerIdentity("law-scrapper-saos", "0.1.0"))
    other = hub.client_for(EgressHost(name="Other", base_url=OTHER_URL), CallerIdentity("law-scrapper-other", "2.0"))
    return hub, saos, other


@pytest.mark.asyncio
@respx.mock
async def test_a_429_pause_on_one_host_does_not_delay_the_other(clock: FakeClock, waits: list[float]) -> None:
    respx.get(f"{SAOS_URL}/x").mock(return_value=httpx.Response(429, headers={"Retry-After": "30"}))
    respx.get(f"{OTHER_URL}/x").mock(return_value=httpx.Response(200, json={"ok": True}))
    hub, saos, other = two_host_hub(clock)
    try:
        with pytest.raises(SejmApiError):
            await saos.get_json("x")

        assert await other.get_json("x") == {"ok": True}
        assert waits == []

        # The pause is real: the host that asked for it is held back.
        with pytest.raises(SejmApiError):
            await saos.get_json("x")
        assert waits == [pytest.approx(30.0)]
    finally:
        await hub.close()


@pytest.mark.asyncio
@respx.mock
async def test_a_tripped_breaker_on_one_host_leaves_the_other_callable(clock: FakeClock, waits: list[float]) -> None:
    respx.get(f"{SAOS_URL}/x").mock(return_value=httpx.Response(503))
    respx.get(f"{OTHER_URL}/x").mock(return_value=httpx.Response(200, json={"ok": True}))
    hub, saos, other = two_host_hub(clock)
    try:
        for _ in range(5):
            with pytest.raises(ApiUnavailableError):
                await saos.get_json("x")

        assert saos.circuit_state == CircuitState.OPEN
        assert other.circuit_state == CircuitState.CLOSED
        assert await other.get_json("x") == {"ok": True}
        assert hub.source_states() == (
            SourceState("SAOS", CircuitState.OPEN),
            SourceState("Other", CircuitState.CLOSED),
        )
    finally:
        await hub.close()


@pytest.mark.asyncio
@respx.mock
async def test_exhausting_one_hosts_bucket_does_not_consume_the_others(clock: FakeClock, waits: list[float]) -> None:
    respx.get(f"{SAOS_URL}/x").mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{OTHER_URL}/x").mock(return_value=httpx.Response(200, json={}))
    hub, saos, other = two_host_hub(clock, rate_per_second=1.0, rate_burst=2)
    try:
        await saos.get_json("x")
        await saos.get_json("x")
        assert waits == []

        # SAOS's bucket is empty; Other's is untouched and answers its whole burst at once.
        await other.get_json("x")
        await other.get_json("x")
        assert waits == []

        await saos.get_json("x")
        assert waits == [pytest.approx(1.0)]
    finally:
        await hub.close()


@pytest.mark.asyncio
@respx.mock
async def test_a_module_host_receives_exactly_the_identity_and_accept_headers(clock: FakeClock) -> None:
    route = respx.get(f"{SAOS_URL}/x").mock(return_value=httpx.Response(200, json={}))
    hub, saos, _other = two_host_hub(clock)
    try:
        await saos.get_json("x")
    finally:
        await hub.close()

    headers = route.calls.last.request.headers
    # What httpx adds on its own (host, encodings, connection) plus the two this project sets.
    assert set(headers.keys()) == {"host", "accept", "accept-encoding", "connection", "user-agent"}
    assert headers["user-agent"] == "law-scrapper-mcp/test law-scrapper-saos/0.1.0"
    assert headers["accept"] == "application/json"
    assert "mozilla" not in headers["user-agent"].lower()


@pytest.mark.asyncio
@respx.mock
async def test_a_cookie_set_by_one_host_is_not_sent_to_the_other(clock: FakeClock) -> None:
    respx.get(f"{SAOS_URL}/login").mock(
        return_value=httpx.Response(200, json={}, headers={"Set-Cookie": "session=abc; Path=/"})
    )
    other_route = respx.get(f"{OTHER_URL}/x").mock(return_value=httpx.Response(200, json={}))
    hub, saos, other = two_host_hub(clock)
    try:
        await saos.get_json("login")
        await other.get_json("x")
    finally:
        await hub.close()

    assert "cookie" not in other_route.calls.last.request.headers


@pytest.mark.asyncio
@respx.mock
async def test_the_other_hosts_request_goes_out_on_its_own_connection_state(clock: FakeClock) -> None:
    hub, saos, other = two_host_hub(clock)
    try:
        await saos.start()
        await other.start()

        assert saos._client is not None
        assert other._client is not None
        assert saos._client is not other._client
    finally:
        await hub.close()
