"""Tests for the HTTP bootstrap that replaces `MCPServer.run()`."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import uvicorn

from law_scrapper_mcp import server as server_module


@pytest.fixture(autouse=True)
def isolate_logging_setup(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep `main()` from reconfiguring the process on the way past.

    `main()` calls `setup_logging()`, which strips every root handler (the one
    `caplog` needs included) and reconfigures `sys.stderr` — under pytest that
    is the capture stream, and nothing here restores it. Neither effect is what
    these tests are about, so the call is stubbed rather than undone.
    """
    monkeypatch.setattr(server_module, "setup_logging", lambda *args, **kwargs: None)
    yield


def test_uvicorn_config_carries_the_graceful_shutdown_window() -> None:
    """The whole reason this project owns its bootstrap.

    `MCPServer.run_streamable_http_async` builds its own `uvicorn.Config` with
    host, port and log level only (mcp/server/mcpserver/server.py:1082-1088),
    so the SDK offers no channel for this option.
    """
    config = server_module.build_uvicorn_config()

    assert config.timeout_graceful_shutdown == server_module.settings.shutdown_grace


def test_uvicorn_config_follows_the_configured_window(monkeypatch) -> None:
    monkeypatch.setattr(server_module.settings, "shutdown_grace", 25.0)

    config = server_module.build_uvicorn_config()

    assert config.timeout_graceful_shutdown == 25.0


def test_http_app_still_serves_the_health_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """`streamable_http_app()` must carry `custom_route` registrations over.

    If it did not, moving off `app.run()` would silently drop `/health`.
    Rate limiting is disabled here because it wraps the returned ASGI app,
    which would otherwise hide `.routes` behind `RateLimitMiddleware` — a
    concern this test does not cover (see `tests/unit/test_rate_limit.py`).
    """
    monkeypatch.setattr(server_module.settings, "rate_limit_enabled", False)

    # `build_http_app()` always wraps the app in `ExemptPathCredentialStripper`
    # (a security property that must not depend on rate limiting being on),
    # so reach through it to inspect the routes underneath.
    paths = {getattr(route, "path", None) for route in server_module.build_http_app()._app.routes}

    assert "/health" in paths
    assert "/mcp" in paths


def test_http_app_pins_security_critical_kwargs(monkeypatch) -> None:
    """`transport_security` and `stateless_http` must reach the SDK call.

    This project runs with `LAW_MCP_HOST=0.0.0.0` in Docker, which defeats the
    SDK's own auto-protection fallback (it only auto-enables DNS-rebinding
    protection when host is `127.0.0.1`/`localhost`/`::1`). If
    `transport_security` were silently dropped here, the allowlist would be
    gone with nothing in the unit suite catching it.
    """
    calls: list[dict[str, object]] = []

    def recording_streamable_http_app(**kwargs: object) -> object:
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(server_module.app, "streamable_http_app", recording_streamable_http_app)

    server_module.build_http_app()

    assert len(calls) == 1
    # `build_transport_security()` reads `Settings` fresh on every call rather
    # than returning a shared singleton, so this compares by value.
    assert calls[0]["transport_security"] == server_module.build_transport_security()
    # The value itself, not just its presence: nothing else in the unit suite
    # asserts DNS-rebinding protection is actually on (the only other proof is
    # an integration-marked live-subprocess test).
    assert calls[0]["transport_security"].enable_dns_rebinding_protection is True
    assert calls[0]["stateless_http"] is True
    assert calls[0]["host"] == server_module.settings.host
    assert calls[0]["streamable_http_path"] == "/mcp"


def test_main_http_branch_uses_our_own_server(monkeypatch) -> None:
    monkeypatch.setattr(server_module.settings, "transport", "streamable-http")
    served: list[uvicorn.Config] = []

    class RecordingServer:
        def __init__(self, config: uvicorn.Config) -> None:
            served.append(config)

        def run(self) -> None:
            return None

    monkeypatch.setattr(server_module.uvicorn, "Server", RecordingServer)
    monkeypatch.setattr(
        server_module.app,
        "run",
        lambda *args, **kwargs: pytest.fail("the HTTP branch must not go through app.run()"),
    )

    server_module.main()

    assert len(served) == 1
    assert served[0].port == server_module.settings.port
    assert served[0].timeout_graceful_shutdown == server_module.settings.shutdown_grace


def test_main_stdio_branch_is_untouched(monkeypatch) -> None:
    """O6: the default transport must not change behaviour."""
    monkeypatch.setattr(server_module.settings, "transport", "stdio")
    calls: list[dict[str, object]] = []

    def fail_on_server(config: uvicorn.Config) -> None:
        pytest.fail("stdio must not build a uvicorn server")

    monkeypatch.setattr(server_module.uvicorn, "Server", fail_on_server)
    monkeypatch.setattr(server_module.app, "run", lambda **kwargs: calls.append(kwargs))

    server_module.main()

    assert calls == [{"transport": "stdio"}]


def test_uvicorn_does_not_rewrite_the_client_by_default() -> None:
    """`LAW_MCP_TRUSTED_PROXIES` must be the only gate on `X-Forwarded-For`.

    uvicorn defaults `proxy_headers` to True and wraps the application in
    `ProxyHeadersMiddleware` from the outside, so for any peer matching its own
    `forwarded_allow_ips` — which falls back to `127.0.0.1`, and this server
    binds loopback by default — it rewrites `scope["client"]` from the header
    before our rate limiter reads it. Any local process could then choose its
    own bucket, which is what criterion 11 forbids. The unit tests would not
    notice, because they drive the ASGI app directly with uvicorn out of the
    stack.
    """
    config = server_module.build_uvicorn_config()

    assert config.proxy_headers is False
    assert config.forwarded_allow_ips == []


def test_uvicorn_refuses_websocket_upgrades() -> None:
    """No route speaks WebSocket, and the middlewares only handle `http`.

    Starlette's `AuthenticationMiddleware` also handles `websocket` scopes and
    calls the verifier on them, while `RateLimitMiddleware` and
    `ExemptPathCredentialStripper` pass anything non-http straight through — so
    an upgrade would reach `verify_token` past both the budget and the strip.
    Leaving uvicorn's `ws="auto"` made that hinge on whether an optional package
    was installed rather than on a decision.
    """
    assert server_module.build_uvicorn_config().ws == "none"


def test_uvicorn_rewrite_stays_off_even_with_trusted_proxies(monkeypatch) -> None:
    """Declaring a proxy must not switch uvicorn's own rewrite back on.

    Tying `proxy_headers` to `trusted_proxies` was tried and reverted:
    `ProxyHeadersMiddleware` writes the header into `scope["client"]` without
    checking it is an address, and it runs before ours, so `_client_key` would
    take a forged string as the peer and never reach its own address check.
    `_client_key` owns `X-Forwarded-For` end to end instead.
    """
    monkeypatch.setattr(server_module.settings, "trusted_proxies", ["10.0.0.1"])

    config = server_module.build_uvicorn_config()

    assert config.proxy_headers is False
    assert config.forwarded_allow_ips == []


@pytest.mark.parametrize("rate_limit_enabled", [True, False])
def test_health_credential_is_stripped(monkeypatch: pytest.MonkeyPatch, rate_limit_enabled: bool) -> None:
    """The strip must not inherit `LAW_MCP_RATE_LIMIT_ENABLED`.

    It first lived inside `RateLimitMiddleware`, and `build_http_app()` returns
    the bare app when rate limiting is off — so a setting about request budgets
    silently decided whether a credential could reach the token verifier on the
    one unmetered path.

    Both branches, deliberately. Covering only the disabled one left the
    *default* branch — the one that actually deploys — unasserted: a mutation
    that skipped the strip solely when wrapping the limiter passed the whole
    suite. That is the same defect shape one layer up, in the tests.
    """
    monkeypatch.setattr(server_module.settings, "rate_limit_enabled", rate_limit_enabled)
    seen: list[list[bytes]] = []

    async def _record(scope, receive, send) -> None:
        seen.append([name for name, _ in scope["headers"]])

    monkeypatch.setattr(server_module.app, "streamable_http_app", lambda **kwargs: _record)

    http_app = server_module.build_http_app()
    scope = {
        "type": "http",
        "path": "/health",
        # The limiter reads the peer address, so it must be present for the
        # enabled branch to get as far as the inner app.
        "client": ("127.0.0.1", 50000),
        "headers": [(b"authorization", b"Bearer forged.jwt.value"), (b"accept", b"*/*")],
    }
    import asyncio

    asyncio.run(http_app(scope, None, None))

    assert seen == [[b"accept"]]
