"""The hub: identity, ownership of an origin, the shared ceiling, the list of active sources."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest
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
from law_scrapper_mcp.client.exceptions import EgressConfigError
from law_scrapper_mcp.client.sejm_client import SejmApiClient

CORE_AGENT = "law-scrapper-mcp/4.4.2 (+https://example.test/repo)"
SAOS = CallerIdentity("law-scrapper-saos", "0.1.0")
SAOS_URL = "https://saos.example.test/api"


def make_hub(**overrides: object) -> EgressHub:
    defaults = EgressDefaults(user_agent=CORE_AGENT, **overrides)  # type: ignore[arg-type]
    return EgressHub(TTLCache(max_entries=50), defaults)


# --- identity ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("module", "version"),
    [
        ("law saos", "0.1.0"),
        ("law-scrapper/saos", "0.1.0"),
        ("law-scrapper-saos", "0.1 0"),
        ("law-scrapper-saos", "0.1.0)"),
        ("(saos)", "0.1.0"),
        ("", "0.1.0"),
        ("law-scrapper-saos", ""),
        ("law-scrapper-saos\r\nX-Evil: 1", "0.1.0"),
    ],
)
def test_a_caller_identity_must_be_rfc_9110_tokens(module: str, version: str) -> None:
    with pytest.raises(EgressConfigError):
        CallerIdentity(module, version)


def test_a_caller_identity_exposes_its_product_token() -> None:
    assert SAOS.product == "law-scrapper-saos/0.1.0"


@pytest.mark.asyncio
@respx.mock
async def test_a_module_client_sends_the_core_identity_plus_its_own_token() -> None:
    route = respx.get(f"{SAOS_URL}/x").mock(return_value=httpx.Response(200, json={}))
    hub = make_hub()
    client = hub.client_for(EgressHost(name="SAOS", base_url=SAOS_URL), SAOS)
    try:
        await client.get_json("x")
    finally:
        await hub.close()

    assert route.calls.last.request.headers["user-agent"] == f"{CORE_AGENT} law-scrapper-saos/0.1.0"


@pytest.mark.asyncio
@respx.mock
async def test_the_adopted_sejm_client_keeps_its_identity_byte_for_byte() -> None:
    route = respx.get(f"{SejmApiClient.BASE_URL}/acts/DU/2024/1").mock(return_value=httpx.Response(200, json={}))
    sejm = SejmApiClient(cache=TTLCache(max_entries=10), user_agent=CORE_AGENT)
    hub = make_hub()
    hub.adopt(sejm)
    try:
        await sejm.get_act("DU", 2024, 1)
    finally:
        await sejm.close()

    assert route.calls.last.request.headers["user-agent"] == CORE_AGENT


@pytest.mark.parametrize("header", ["User-Agent", "user-agent", "USER-AGENT", "Host", "host"])
def test_a_host_cannot_override_the_identity_or_host_header(header: str) -> None:
    with pytest.raises(EgressConfigError):
        EgressHost(name="SAOS", base_url=SAOS_URL, headers={header: "x"})


@pytest.mark.asyncio
@respx.mock
async def test_a_host_can_replace_the_default_accept_header() -> None:
    route = respx.get(f"{SAOS_URL}/x").mock(return_value=httpx.Response(200, json={}))
    hub = make_hub()
    client = hub.client_for(
        EgressHost(name="SAOS", base_url=SAOS_URL, headers={"Accept": "application/vnd.saos+json"}), SAOS
    )
    try:
        await client.get_json("x")
    finally:
        await hub.close()

    assert route.calls.last.request.headers["accept"] == "application/vnd.saos+json"


# --- host validation ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://saos.example.test/api",
        "saos.example.test/api",
        "https:///api",
        "https://user:pw@saos.example.test/api",
        "https://user@saos.example.test/api",
        "https://saos.example.test/api?token=1",
        "https://saos.example.test/api#frag",
        "https://saos.example.test:notaport/api",
    ],
)
def test_an_unusable_base_url_is_rejected(base_url: str) -> None:
    with pytest.raises(EgressConfigError):
        EgressHost(name="SAOS", base_url=base_url)


def test_a_trailing_slash_of_the_base_url_is_stripped() -> None:
    assert EgressHost(name="SAOS", base_url=f"{SAOS_URL}/").base_url == SAOS_URL


@pytest.mark.parametrize(
    "name",
    ["", " ", " SAOS", "SAOS ", "SA/OS", "SA:OS", "SAOS\n", "x" * 65, "SÃOS", "<b>SAOS</b>"],
)
def test_a_source_label_is_a_short_plain_name(name: str) -> None:
    with pytest.raises(EgressConfigError):
        EgressHost(name=name, base_url=SAOS_URL)


@pytest.mark.parametrize("name", ["SAOS", "api.sejm.gov.pl", "Sad Najwyzszy", "x" * 64, "a_b-c.d 1"])
def test_a_valid_source_label_is_accepted(name: str) -> None:
    assert EgressHost(name=name, base_url=SAOS_URL).name == name


# --- ownership of an origin and of a name ------------------------------------------------


def test_a_second_client_for_the_same_origin_is_refused() -> None:
    hub = make_hub()
    hub.client_for(EgressHost(name="SAOS", base_url=SAOS_URL), SAOS)

    with pytest.raises(EgressConfigError):
        hub.client_for(EgressHost(name="Other", base_url=f"{SAOS_URL}/v2"), CallerIdentity("other", "1"))


def test_the_sejm_origin_cannot_be_claimed_by_a_module() -> None:
    hub = make_hub()
    hub.adopt(SejmApiClient(cache=TTLCache(max_entries=10)))

    with pytest.raises(EgressConfigError):
        hub.client_for(EgressHost(name="Mirror", base_url="https://api.sejm.gov.pl/other"), SAOS)


def test_adopting_the_same_origin_twice_is_refused() -> None:
    hub = make_hub()
    hub.adopt(SejmApiClient(cache=TTLCache(max_entries=10)))

    with pytest.raises(EgressConfigError):
        hub.adopt(SejmApiClient(cache=TTLCache(max_entries=10)))


def test_a_different_port_is_a_different_origin() -> None:
    hub = make_hub()
    hub.client_for(EgressHost(name="A", base_url="https://h.example.test/api"), SAOS)

    hub.client_for(EgressHost(name="B", base_url="https://h.example.test:8443/api"), CallerIdentity("b", "1"))

    assert [state.name for state in hub.source_states()] == ["A", "B"]


def test_a_source_name_can_be_used_once() -> None:
    hub = make_hub()
    hub.client_for(EgressHost(name="SAOS", base_url=SAOS_URL), SAOS)

    with pytest.raises(EgressConfigError):
        hub.client_for(EgressHost(name="SAOS", base_url="https://elsewhere.example.test/api"), CallerIdentity("b", "1"))


# --- ceiling ------------------------------------------------------------------------------


def test_a_module_cannot_ask_for_more_than_the_hub_ceiling() -> None:
    hub = make_hub(rate_per_second=5.0, rate_burst=10)

    with pytest.raises(EgressConfigError):
        hub.client_for(EgressHost(name="A", base_url=SAOS_URL, rate_per_second=5.5), SAOS)
    with pytest.raises(EgressConfigError):
        hub.client_for(EgressHost(name="A", base_url=SAOS_URL, rate_burst=11), SAOS)
    # A refused request registers nothing, so the origin and the name are still free.
    assert hub.source_states() == ()


def test_a_module_may_lower_the_hub_ceiling() -> None:
    hub = make_hub(rate_per_second=5.0, rate_burst=10)

    client = hub.client_for(EgressHost(name="A", base_url=SAOS_URL, rate_per_second=1.0, rate_burst=2), SAOS)

    assert client._rate_limiter._rate == 1.0
    assert client._rate_limiter._burst == 2.0


def test_a_host_without_a_rate_gets_the_hub_ceiling() -> None:
    hub = make_hub(rate_per_second=4.0, rate_burst=7)

    client = hub.client_for(EgressHost(name="A", base_url=SAOS_URL), SAOS)

    assert client._rate_limiter._rate == 4.0
    assert client._rate_limiter._burst == 7.0


@pytest.mark.parametrize(
    "host_kwargs",
    [{"pin_origin": False}, {"max_redirects": 4}, {"max_redirects": 20}, {"pin_origin": False, "max_redirects": 20}],
)
def test_a_module_cannot_opt_out_of_the_origin_pin_or_raise_the_redirect_cap(host_kwargs: dict[str, object]) -> None:
    hub = make_hub()

    with pytest.raises(EgressConfigError):
        hub.client_for(EgressHost(name="A", base_url=SAOS_URL, **host_kwargs), SAOS)  # type: ignore[arg-type]


def test_a_module_may_lower_the_redirect_cap() -> None:
    hub = make_hub()

    client = hub.client_for(EgressHost(name="A", base_url=SAOS_URL, max_redirects=0), SAOS)

    assert client.host.max_redirects == 0


# --- per-host state is never shared ------------------------------------------------------


def test_each_client_has_its_own_limiter_breaker_and_semaphores() -> None:
    hub = make_hub()
    a = hub.client_for(EgressHost(name="A", base_url="https://a.example.test"), CallerIdentity("a", "1"))
    b = hub.client_for(EgressHost(name="B", base_url="https://b.example.test"), CallerIdentity("b", "1"))

    assert a._rate_limiter is not b._rate_limiter
    assert a._circuit_breaker is not b._circuit_breaker
    assert a._semaphores[next(iter(a._semaphores))] is not b._semaphores[next(iter(b._semaphores))]
    assert a._cache is b._cache


# --- the list of active sources ----------------------------------------------------------


def test_source_states_lists_name_and_state_in_registration_order() -> None:
    hub = make_hub()
    sejm = SejmApiClient(cache=TTLCache(max_entries=10))
    hub.adopt(sejm)
    hub.client_for(EgressHost(name="SAOS", base_url=SAOS_URL), SAOS)
    hub.client_for(EgressHost(name="Other", base_url="https://other.example.test"), CallerIdentity("o", "1"))

    assert hub.source_states() == (
        SourceState("api.sejm.gov.pl", CircuitState.CLOSED),
        SourceState("SAOS", CircuitState.CLOSED),
        SourceState("Other", CircuitState.CLOSED),
    )


def test_source_states_exposes_only_a_name_and_a_state() -> None:
    assert set(SourceState.__dataclass_fields__) == {"name", "circuit_state"}


def test_source_states_reports_an_open_breaker_without_moving_it() -> None:
    hub = make_hub()
    client = hub.client_for(EgressHost(name="SAOS", base_url=SAOS_URL), SAOS)
    for _ in range(5):
        client._circuit_breaker.try_acquire()
        client._circuit_breaker.release_failure()

    assert hub.source_states() == (SourceState("SAOS", CircuitState.OPEN),)
    assert hub.source_states() == (SourceState("SAOS", CircuitState.OPEN),)


# --- close -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_closes_the_owned_clients_and_collects_every_error(monkeypatch: pytest.MonkeyPatch) -> None:
    hub = make_hub()
    clients = [
        hub.client_for(EgressHost(name=name, base_url=f"https://{name.lower()}.example.test"), CallerIdentity("m", "1"))
        for name in ("A", "B", "C")
    ]
    closed: list[str] = []

    def failing(client: EgressClient) -> Callable[[], Awaitable[None]]:
        async def close() -> None:
            raise RuntimeError(f"close {client.host.name} failed")

        return close

    def recording(client: EgressClient) -> Callable[[], Awaitable[None]]:
        async def close() -> None:
            closed.append(client.host.name)

        return close

    monkeypatch.setattr(clients[0], "close", failing(clients[0]))
    monkeypatch.setattr(clients[1], "close", failing(clients[1]))
    monkeypatch.setattr(clients[2], "close", recording(clients[2]))

    with pytest.raises(ExceptionGroup) as excinfo:
        await hub.close()

    assert sorted(str(exc) for exc in excinfo.value.exceptions) == ["close A failed", "close B failed"]
    # The failing closes did not stop the third one.
    assert closed == ["C"]


@pytest.mark.asyncio
async def test_close_leaves_an_adopted_client_to_its_owner() -> None:
    hub = make_hub()
    sejm = SejmApiClient(cache=TTLCache(max_entries=10))
    await sejm.start()
    hub.adopt(sejm)

    await hub.close()

    assert sejm._client is not None
    await sejm.close()
