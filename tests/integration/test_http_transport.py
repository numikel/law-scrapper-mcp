"""Integration tests for HTTP transport."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from law_scrapper_mcp.config import settings
from law_scrapper_mcp.server import app

pytestmark = pytest.mark.integration


def test_health_endpoint() -> None:
    """GET /health returns status, version, and server name."""
    asgi_app = app.http_app(path="/mcp")
    with TestClient(asgi_app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == settings.server_version
    assert data["server"] == settings.server_name
