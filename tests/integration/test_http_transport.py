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
from law_scrapper_mcp.server import app

pytestmark = pytest.mark.integration
PROJECT_ROOT = Path(__file__).parents[2]
PROTOCOL_FLOOR = date(2026, 7, 28)


@pytest.fixture
def asgi_app():
    return app.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        host="testserver",
    )


def _rpc(client: TestClient, method: str, params: dict[str, object], request_id: int):
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Mcp-Method": method,
        "Mcp-Protocol-Version": LATEST_PROTOCOL_VERSION,
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
    """GET /health returns status, version, and server name."""
    with TestClient(asgi_app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == settings.server_version
    assert data["server"] == settings.server_name


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
    assert discover.json()["result"]["supportedVersions"]
    assert len(listed.json()["result"]["tools"]) == 13
    assert success.json()["result"]["isError"] is False
    assert failure.json()["result"]["isError"] is True


@pytest.fixture
def live_http_server(tmp_path: Path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    log_path = tmp_path / "http-server.log"
    environment = {
        **os.environ,
        "LAW_MCP_TRANSPORT": "streamable-http",
        "LAW_MCP_HOST": "127.0.0.1",
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
            deadline = time.monotonic() + 10
            health_url = f"http://127.0.0.1:{port}/health"
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                try:
                    if httpx.get(health_url, timeout=0.25).status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(0.1)
            else:
                pytest.fail(
                    f"HTTP server did not become ready:\n"
                    f"{log_path.read_text(encoding='utf-8')}"
                )
            if process.poll() is not None:
                pytest.fail(
                    f"HTTP server exited early:\n"
                    f"{log_path.read_text(encoding='utf-8')}"
                )
            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


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
