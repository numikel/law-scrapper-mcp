"""The authenticated HTTP surface: 401 without a token, MCP with one."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from law_scrapper_mcp import server as server_module
from law_scrapper_mcp.auth import build_auth
from law_scrapper_mcp.config import Settings

pytestmark = pytest.mark.integration

TOKEN = "k" * 32
LOOPBACK_HOST_HEADER = "127.0.0.1:7683"


@pytest.fixture
def bearer_app(monkeypatch: pytest.MonkeyPatch):
    """Reconfigure the live server module for bearer auth without reloading it.

    `importlib.reload(config)` would rebuild the `settings` singleton that five
    service modules already captured by value at their own import time,
    leaving them out of sync with `server.settings` until a second reload — a
    global side effect that survives a setup failure (a reload raising
    partway through skips this generator's post-`yield` teardown entirely).

    Instead, this patches only what `server.py`'s call graph actually reads at
    request time: the module-level `settings` name (read fresh by
    `build_http_app`/`build_transport_security`/`health`, since Python looks up
    module globals by name on every call) and the already-constructed `app`
    instance's `settings.auth` / `_token_verifier` (read fresh by
    `MCPServer.streamable_http_app` on every call — confirmed in the SDK
    source, not assumed). Nothing under `sys.modules` is touched, so the five
    service modules' own `settings` references are untouched by construction,
    not by convention.

    Every mutation goes through `monkeypatch.setattr`, whose own fixture
    (`yield mpatch; mpatch.undo()`) records each change immediately and undoes
    it in its own guaranteed teardown — independent of whether this fixture's
    body raises before reaching `yield`. There is nothing left for a manual
    `try/finally` to protect that `monkeypatch` doesn't already protect.
    """
    bearer_settings = Settings(
        transport="streamable-http",
        auth_mode="bearer",
        auth_token=TOKEN,
        rate_limit_enabled=False,
    )
    auth_settings, token_verifier = build_auth(bearer_settings)

    monkeypatch.setattr(server_module, "settings", bearer_settings)
    monkeypatch.setattr(server_module.app.settings, "auth", auth_settings)
    monkeypatch.setattr(server_module.app, "_token_verifier", token_verifier)

    yield server_module.build_http_app()


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
    assert response.status_code == 200
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
    assert str(auth_settings.issuer_url) == "http://127.0.0.1:7683/"


def test_none_mode_builds_no_auth() -> None:
    """`auth=None` with no verifier is an explicit state, not an empty token."""
    assert build_auth(Settings()) == (None, None)


def test_bearer_mode_brackets_an_ipv6_host_in_the_issuer_url() -> None:
    """An unbracketed IPv6 literal is not a valid URL authority.

    `LAW_MCP_HOST=::` (dual-stack Docker bind) with `auth_mode=bearer` passes
    `Settings`' own validator — the non-loopback rejection only fires for
    `auth_mode=none` — so this must not crash `build_auth()` at import time.
    """
    auth_settings, verifier = build_auth(Settings(auth_mode="bearer", auth_token=TOKEN, host="::1"))
    assert auth_settings is not None and verifier is not None
    assert str(auth_settings.issuer_url) == "http://[::1]:7683/"


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
    assert str(auth_settings.resource_server_url) == "https://mcp.example.com/mcp"
