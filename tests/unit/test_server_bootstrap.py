"""Tests for the HTTP bootstrap that replaces `MCPServer.run()`."""

from __future__ import annotations

import pytest
import uvicorn

from law_scrapper_mcp import server as server_module


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


def test_http_app_still_serves_the_health_route() -> None:
    """`streamable_http_app()` must carry `custom_route` registrations over.

    If it did not, moving off `app.run()` would silently drop `/health`.
    """
    paths = {getattr(route, "path", None) for route in server_module.build_http_app().routes}

    assert "/health" in paths
    assert "/mcp" in paths


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
