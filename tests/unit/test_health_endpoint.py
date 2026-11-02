"""`/health` reports upstream state without lying about process health."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from law_scrapper_mcp import server as server_module
from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.circuit_breaker import CircuitBreaker, CircuitState
from law_scrapper_mcp.client.egress import CallerIdentity, EgressDefaults, EgressHost, EgressHub
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.config import settings


@pytest.fixture
def client() -> TestClient:
    """Drive the real ASGI app without running its lifespan.

    Entering the TestClient context manager would start the MCP lifespan and
    populate the handle, making the `unknown` case untestable. Starlette builds
    a portal per request, so plain `.get()` works without the context manager.
    """
    return TestClient(server_module.build_http_app())


@pytest.fixture(autouse=True)
def clean_health_state():
    """Module-level state must not leak between tests."""
    server_module._health_state.clear()
    yield
    server_module._health_state.clear()


def test_health_reports_unknown_before_lifespan_starts(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == settings.server_version
    assert body["server"] == settings.server_name
    assert body["upstream"] == {"circuit_state": "unknown"}


def test_health_reports_a_closed_breaker(client: TestClient) -> None:
    server_module._health_state.set(CircuitBreaker(failure_threshold=5))

    body = client.get("/health").json()

    assert body["upstream"]["circuit_state"] == "closed"
    assert body["upstream"]["failure_count"] == 0


def test_health_stays_200_while_the_breaker_is_open(client: TestClient) -> None:
    """O4: a restart cannot repair an outage of api.sejm.gov.pl.

    Reporting upstream trouble as process ill-health would turn
    `restart: unless-stopped` into a restart loop over someone else's outage.
    """
    breaker = CircuitBreaker(failure_threshold=3)
    for _ in range(3):
        breaker.release_failure()
    assert breaker.state is CircuitState.OPEN
    server_module._health_state.set(breaker)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["upstream"]["circuit_state"] == "open"
    assert body["upstream"]["failure_count"] == 3


def test_health_reports_unknown_again_after_the_handle_is_cleared(client: TestClient) -> None:
    server_module._health_state.set(CircuitBreaker())
    server_module._health_state.clear()

    assert client.get("/health").json()["upstream"] == {"circuit_state": "unknown"}


def test_health_reports_a_half_open_breaker(client: TestClient) -> None:
    """The recovery probe state must be visible too, not only the two extremes (#31).

    Reading `state` is side-effect free; the OPEN → HALF_OPEN transition is
    performed by `try_acquire()` once `recovery_timeout` has elapsed, so with
    `recovery_timeout=0` a single admission attempt is the tick.
    """
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0)
    breaker.release_failure()
    assert breaker.state is CircuitState.OPEN
    assert breaker.try_acquire() is True
    assert breaker.state is CircuitState.HALF_OPEN
    server_module._health_state.set(breaker)

    body = client.get("/health").json()

    assert body["upstream"]["circuit_state"] == "half_open"
    assert body["upstream"]["failure_count"] == 1


# --- `sources`: every active source by name and circuit state ---------------------------------


def _hub_with_a_module_host(*, trip_module_breaker: bool) -> EgressHub:
    hub = EgressHub(TTLCache(max_entries=10), EgressDefaults(user_agent="law-scrapper-mcp/test"))
    hub.adopt(SejmApiClient(cache=TTLCache(max_entries=10)))
    module_client = hub.client_for(
        EgressHost(name="SAOS", base_url="https://saos.example.test/api"), CallerIdentity("law-scrapper-saos", "0.1.0")
    )
    if trip_module_breaker:
        for _ in range(5):
            module_client._circuit_breaker.release_failure()
    return hub


def test_health_lists_no_sources_before_the_lifespan_and_after_clear(client: TestClient) -> None:
    assert client.get("/health").json()["sources"] == []

    server_module._health_state.set(CircuitBreaker(), _hub_with_a_module_host(trip_module_breaker=False))
    server_module._health_state.clear()

    assert client.get("/health").json()["sources"] == []


def test_health_lists_the_sejm_and_a_module_host_with_exactly_two_keys(client: TestClient) -> None:
    server_module._health_state.set(CircuitBreaker(), _hub_with_a_module_host(trip_module_breaker=False))

    body = client.get("/health").json()

    assert body["sources"] == [
        {"name": "api.sejm.gov.pl", "circuit_state": "closed"},
        {"name": "SAOS", "circuit_state": "closed"},
    ]


def test_an_open_module_breaker_leaves_status_200_and_the_upstream_block_unchanged(client: TestClient) -> None:
    server_module._health_state.set(CircuitBreaker(), _hub_with_a_module_host(trip_module_breaker=True))

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["upstream"] == {"circuit_state": "closed", "failure_count": 0}
    assert body["sources"][1] == {"name": "SAOS", "circuit_state": "open"}


def test_health_keeps_every_pre_existing_key_and_adds_only_sources(client: TestClient) -> None:
    assert set(client.get("/health").json()) == {"status", "version", "server", "upstream", "sources"}


def test_set_without_a_hub_keeps_working_and_lists_no_sources(client: TestClient) -> None:
    server_module._health_state.set(CircuitBreaker())

    body = client.get("/health").json()

    assert body["upstream"]["circuit_state"] == "closed"
    assert body["sources"] == []
