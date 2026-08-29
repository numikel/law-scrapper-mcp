"""The authenticated HTTP surface: 401 without a token, MCP with one."""

from __future__ import annotations

import importlib

import pytest
from starlette.testclient import TestClient

from law_scrapper_mcp.auth import build_auth
from law_scrapper_mcp.config import Settings

pytestmark = pytest.mark.integration

TOKEN = "k" * 32
LOOPBACK_HOST_HEADER = "127.0.0.1:7683"


@pytest.fixture
def bearer_app(monkeypatch: pytest.MonkeyPatch):
    """Rebuild the server module against a bearer-mode configuration.

    `MCPServer` reads `auth` and `token_verifier` in its constructor, so the
    module must be re-imported after the settings change — patching afterwards
    would leave the SDK's middleware stack unbuilt.
    """
    monkeypatch.setenv("LAW_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("LAW_MCP_AUTH_MODE", "bearer")
    monkeypatch.setenv("LAW_MCP_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("LAW_MCP_RATE_LIMIT_ENABLED", "false")
    # `server.py` binds `settings` by value at import time (`from ...config import
    # settings`); reloading only `server` would leave it pointing at the module-level
    # singleton built under the pre-monkeypatch environment. Reload `config` first so
    # a fresh `Settings()` picks up the patched env vars, then `server` picks up that.
    importlib.reload(importlib.import_module("law_scrapper_mcp.config"))
    server_module = importlib.reload(importlib.import_module("law_scrapper_mcp.server"))
    yield server_module.build_http_app()
    monkeypatch.undo()
    importlib.reload(importlib.import_module("law_scrapper_mcp.config"))
    importlib.reload(importlib.import_module("law_scrapper_mcp.server"))


def _mcp_headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Host": LOOPBACK_HOST_HEADER,
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def test_mcp_without_token_is_401(bearer_app) -> None:
    """Criterion 7, first half."""
    with TestClient(bearer_app) as client:
        response = client.post("/mcp", headers=_mcp_headers(None), json={})
    assert response.status_code == 401


def test_mcp_with_wrong_token_is_401(bearer_app) -> None:
    with TestClient(bearer_app) as client:
        response = client.post("/mcp", headers=_mcp_headers("w" * 32), json={})
    assert response.status_code == 401


def test_authenticated_tools_list_has_13_tools(bearer_app) -> None:
    """Criteria 7 and 8: the auth layer must not touch the tool surface."""
    with TestClient(bearer_app) as client:
        response = client.post(
            "/mcp",
            headers=_mcp_headers(TOKEN),
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
    assert response.status_code != 401
    body = response.text
    assert "search_legal_acts" in body
    assert body.count('"name"') >= 13


def test_health_stays_unauthenticated(bearer_app) -> None:
    """RequireAuthMiddleware wraps /mcp only (mcp/server/lowlevel/server.py:801-803)."""
    with TestClient(bearer_app) as client:
        assert client.get("/health").status_code == 200


def test_bearer_mode_advertises_no_resource_metadata() -> None:
    """Criterion 19 (D16): the server runs no OAuth flow, so it advertises none."""
    auth_settings, verifier = build_auth(Settings(transport="streamable-http", auth_mode="bearer", auth_token=TOKEN))
    assert auth_settings is not None and verifier is not None
    assert auth_settings.resource_server_url is None
    assert str(auth_settings.issuer_url).startswith("http://127.0.0.1")


def test_none_mode_builds_no_auth() -> None:
    """`auth=None` with no verifier is an explicit state, not an empty token."""
    assert build_auth(Settings()) == (None, None)


def test_oauth_mode_builds_the_jwt_verifier() -> None:
    from law_scrapper_mcp.auth.jwt_verifier import JwtTokenVerifier

    auth_settings, verifier = build_auth(
        Settings(
            auth_mode="oauth",
            auth_issuer="https://login.example.com/tenant/v2.0",
            auth_audience="api://law-scrapper",
            auth_resource_server_url="https://mcp.example.com/mcp",
        )
    )
    assert isinstance(verifier, JwtTokenVerifier)
    assert auth_settings is not None
    assert str(auth_settings.resource_server_url).startswith("https://mcp.example.com")
