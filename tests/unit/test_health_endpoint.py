"""`/health` reports upstream state without lying about process health."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from law_scrapper_mcp import server as server_module
from law_scrapper_mcp.client.circuit_breaker import CircuitBreaker, CircuitState
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
