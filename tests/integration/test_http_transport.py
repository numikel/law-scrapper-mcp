"""Integration tests for HTTP transport."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import httpx
import pytest
from mcp import Client
from mcp.types import CLIENT_CAPABILITIES_META_KEY, LATEST_PROTOCOL_VERSION, PROTOCOL_VERSION_META_KEY
from starlette.testclient import TestClient

from law_scrapper_mcp.config import settings
from law_scrapper_mcp.server import LOOPBACK_TRANSPORT_SECURITY, app

pytestmark = pytest.mark.integration
PROJECT_ROOT = Path(__file__).parents[2]
PROTOCOL_FLOOR = date(2026, 7, 28)
PROTOCOL_FLOOR_VERSION = "2026-07-28"
MAX_PORT_BIND_ATTEMPTS = 10
LOOPBACK_HOST_HEADER = "127.0.0.1:7683"


@pytest.fixture
def asgi_app():
    # host="0.0.0.0" mirrors the production bind address (Docker port publishing).
    # The SDK only auto-enables Host/Origin validation for a literal loopback
    # `host`, so this fixture must pass `transport_security` explicitly, exactly
    # like `server.main()` does, or it would silently test a less-protected app
    # than what actually runs.
    return app.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        host="0.0.0.0",
        transport_security=LOOPBACK_TRANSPORT_SECURITY,
    )


def _rpc(client: TestClient, method: str, params: dict[str, object], request_id: int):
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Mcp-Method": method,
        "Mcp-Protocol-Version": LATEST_PROTOCOL_VERSION,
        "Host": LOOPBACK_HOST_HEADER,
    }
    if method == "tools/call":
        headers["Mcp-Name"] = str(params["name"])
    return client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": {
                **params,
                "_meta": {
                    PROTOCOL_VERSION_META_KEY: LATEST_PROTOCOL_VERSION,
                    CLIENT_CAPABILITIES_META_KEY: {},
                },
            },
        },
    )


def test_health_endpoint(asgi_app) -> None:
    """GET /health returns status, version, server name and upstream state."""
    with TestClient(asgi_app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == settings.server_version
    assert data["server"] == settings.server_name
    # The lifespan ran inside the context manager, so the handle is populated.
    assert data["upstream"]["circuit_state"] == "closed"
    assert data["upstream"]["failure_count"] == 0


def test_stateless_http_protocol_matrix(asgi_app) -> None:
    with TestClient(asgi_app) as client:
        discover = _rpc(client, "server/discover", {}, 1)
        listed = _rpc(client, "tools/list", {}, 2)
        success = _rpc(
            client,
            "tools/call",
            {
                "name": "calculate_legal_date",
                "arguments": {"days": 1, "base_date": "2026-01-01"},
            },
            3,
        )
        failure = _rpc(
            client,
            "tools/call",
            {"name": "get_act_details", "arguments": {"eli": "INVALID"}},
            4,
        )

    for response in (discover, listed, success, failure):
        assert response.status_code == 200
        assert "Mcp-Session-Id" not in response.headers

    discover_result = discover.json()["result"]
    supported_versions = discover_result["supportedVersions"]
    assert supported_versions
    assert any(date.fromisoformat(version) >= PROTOCOL_FLOOR for version in supported_versions)
    assert PROTOCOL_FLOOR_VERSION in supported_versions

    assert len(listed.json()["result"]["tools"]) == 13

    success_result = success.json()["result"]
    assert success_result["isError"] is False
    assert success_result["structuredContent"]["data"]["calculated_date"] == "2026-01-02"

    failure_result = failure.json()["result"]
    assert failure_result["isError"] is True
    failure_messages = [
        item["text"] for item in failure_result["content"] if item.get("type") == "text" and item.get("text")
    ]
    assert failure_messages
    assert any("Nieprawidłowy format ELI" in message for message in failure_messages)


def test_forged_host_header_is_rejected(asgi_app) -> None:
    """A Host header outside the loopback allowlist must be rejected with 421.

    Regression test: binding host="0.0.0.0" (this project's Docker default)
    disables the SDK's auto-enabled DNS-rebinding protection, because the SDK
    only auto-enables it for a literal loopback `host` value. Without the
    explicit `transport_security` passed in `server.main()`, this request
    returned 200.
    """
    with TestClient(asgi_app) as client:
        response = client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "Mcp-Method": "server/discover",
                "Mcp-Protocol-Version": LATEST_PROTOCOL_VERSION,
                "Host": "evil.example.com",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {
                    "_meta": {
                        PROTOCOL_VERSION_META_KEY: LATEST_PROTOCOL_VERSION,
                        CLIENT_CAPABILITIES_META_KEY: {},
                    },
                },
            },
        )

    assert response.status_code == 421


def test_forged_origin_header_is_rejected(asgi_app) -> None:
    """An Origin header outside the loopback allowlist must be rejected with 403."""
    with TestClient(asgi_app) as client:
        response = client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "Mcp-Method": "server/discover",
                "Mcp-Protocol-Version": LATEST_PROTOCOL_VERSION,
                "Host": LOOPBACK_HOST_HEADER,
                "Origin": "http://evil.example.com",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {
                    "_meta": {
                        PROTOCOL_VERSION_META_KEY: LATEST_PROTOCOL_VERSION,
                        CLIENT_CAPABILITIES_META_KEY: {},
                    },
                },
            },
        )

    assert response.status_code == 403


def test_legitimate_loopback_origin_is_accepted(asgi_app) -> None:
    """A loopback Origin must still be accepted; the allowlist is not empty-passes-all."""
    with TestClient(asgi_app) as client:
        response = client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "Mcp-Method": "server/discover",
                "Mcp-Protocol-Version": LATEST_PROTOCOL_VERSION,
                "Host": LOOPBACK_HOST_HEADER,
                "Origin": "http://127.0.0.1:5173",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {
                    "_meta": {
                        PROTOCOL_VERSION_META_KEY: LATEST_PROTOCOL_VERSION,
                        CLIENT_CAPABILITIES_META_KEY: {},
                    },
                },
            },
        )

    assert response.status_code == 200


def _allocate_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _stop_http_server(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _wait_for_http_server(
    process: subprocess.Popen[str],
    *,
    port: int,
    deadline_seconds: float = 10,
) -> bool:
    health_url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            if httpx.get(health_url, timeout=0.25).status_code == 200:
                return process.poll() is None
        except httpx.HTTPError:
            time.sleep(0.1)
    return False


@pytest.fixture
def live_http_server(tmp_path: Path):
    log_path = tmp_path / "http-server.log"
    startup_errors: list[str] = []

    for _ in range(MAX_PORT_BIND_ATTEMPTS):
        port = _allocate_loopback_port()
        environment = {
            **os.environ,
            "LAW_MCP_TRANSPORT": "streamable-http",
            # Mirror the production bind address. A literal loopback host would
            # make the SDK enable Host/Origin validation on its own, so the
            # explicit `transport_security` in `main()` could be deleted without
            # any test noticing.
            "LAW_MCP_HOST": "0.0.0.0",
            "LAW_MCP_PORT": str(port),
        }
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "law_scrapper_mcp"],
                cwd=PROJECT_ROOT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                if _wait_for_http_server(process, port=port):
                    yield f"http://127.0.0.1:{port}/mcp"
                    return
                startup_errors.append(f"port {port} failed:\n{log_path.read_text(encoding='utf-8')}")
            finally:
                _stop_http_server(process)

    pytest.fail(
        "HTTP server did not bind and become ready after "
        f"{MAX_PORT_BIND_ATTEMPTS} attempts:\n" + "\n---\n".join(startup_errors)
    )


@pytest.mark.anyio
async def test_live_http_discovery_tools_success_and_error(live_http_server: str) -> None:
    async with Client(live_http_server) as client:
        assert date.fromisoformat(client.protocol_version) >= PROTOCOL_FLOOR
        tools = (await client.list_tools()).tools
        success = await client.call_tool(
            "calculate_legal_date",
            {"days": 1, "base_date": "2026-01-01"},
        )
        failure = await client.call_tool("get_act_details", {"eli": "INVALID"})

    assert len(tools) == 13
    assert success.is_error is False
    assert success.structured_content["data"]["calculated_date"] == "2026-01-02"
    assert failure.is_error is True


@pytest.mark.anyio
async def test_live_server_enforces_allowlist_and_statelessness(live_http_server: str) -> None:
    """Guard `main()` itself.

    Every other transport test builds its own ASGI app and supplies
    `transport_security` from the test side, so deleting it from `main()` would
    not fail any of them. This test drives the real subprocess instead.
    """
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {
            "_meta": {
                PROTOCOL_VERSION_META_KEY: LATEST_PROTOCOL_VERSION,
                CLIENT_CAPABILITIES_META_KEY: {},
            },
        },
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Mcp-Method": "tools/list",
        "Mcp-Protocol-Version": LATEST_PROTOCOL_VERSION,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        forged_host = await client.post(
            live_http_server,
            headers={**headers, "Host": "evil.example.com"},
            json=body,
        )
        forged_origin = await client.post(
            live_http_server,
            headers={**headers, "Origin": "http://evil.example.com"},
            json=body,
        )
        legitimate = await client.post(live_http_server, headers=headers, json=body)

    assert forged_host.status_code == 421
    assert forged_origin.status_code == 403
    assert legitimate.status_code == 200
    assert "Mcp-Session-Id" not in legitimate.headers


@pytest.mark.asyncio
async def test_health_answers_while_a_document_is_converting(
    mock_client,
    document_store,
    act_detail,
    monkeypatch,
) -> None:
    """Spec 5.1, second bullet: `/health` answers in under 1 s mid-conversion.

    The unit-level ticker test proves the event loop stays free, but it drives a
    bare `ActService`. Only this test proves the composition the cluster
    actually rebuilt: a conversion burning a worker thread while a request
    travels the Starlette app from `build_http_app()`, through the
    `custom_route` registration, into the handler that reads the health handle.
    """
    import asyncio

    from law_scrapper_mcp import server as server_module
    from law_scrapper_mcp.services.act_service import ActService
    from law_scrapper_mcp.services.content_processor import ContentProcessor

    class SlowProcessor(ContentProcessor):
        """Burns wall-clock time synchronously, the way markdownify does."""

        def html_to_markdown(self, html: str) -> str:
            time.sleep(1.5)
            return "# Art. 1. Test section"

    async def fake_get_json(path: str, **kwargs: object) -> object:
        if path.endswith("/struct"):
            raise RuntimeError("no structure for this act")
        return act_detail

    async def fake_get_act_html(publisher: str, year: int, pos: int) -> str:
        return "<html><body><h1>Ustawa</h1></body></html>"

    monkeypatch.setattr(mock_client, "get_json", fake_get_json)
    monkeypatch.setattr(mock_client, "get_act_html", fake_get_act_html)

    service = ActService(
        client=mock_client,
        document_store=document_store,
        content_processor=SlowProcessor(),
    )

    load_task = asyncio.create_task(service.get_details("DU/2024/1", load_content=True))
    await asyncio.sleep(0.1)
    assert not load_task.done(), "conversion already finished: it ran on the event loop, not a worker thread"

    transport = httpx.ASGITransport(app=server_module.build_http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        started = time.perf_counter()
        response = await client.get("/health")
        elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed < 1.0
    assert not load_task.done(), "the probe must have overtaken the conversion, not waited it out"

    await load_task
