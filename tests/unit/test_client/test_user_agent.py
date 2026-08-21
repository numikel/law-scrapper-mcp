"""Tests for the identity the client presents to api.sejm.gov.pl (finding F52)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.config import USER_AGENT_CONTACT, Settings

CLIENT_PACKAGE = Path(__file__).parents[3] / "src" / "law_scrapper_mcp"

# Matches a hardcoded "law-scrapper-mcp/<version>" literal — the exact shape that
# drifted two major versions behind the real one before F52 was fixed.
VERSIONED_LITERAL = re.compile(r"law-scrapper-mcp/\d")


def test_user_agent_reports_the_configured_version() -> None:
    """The advertised version must be the one the server actually reports."""
    settings = Settings()
    assert settings.user_agent.startswith(f"{settings.server_name}/{settings.server_version}")


def test_user_agent_tracks_a_version_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Derivation, not duplication: overriding the version moves the header too."""
    monkeypatch.setenv("LAW_MCP_SERVER_VERSION", "9.9.9")
    assert "law-scrapper-mcp/9.9.9" in Settings().user_agent


def test_user_agent_carries_a_contact_channel() -> None:
    """The API is run by a state institution; its admin needs a route other than a ban."""
    assert USER_AGENT_CONTACT in Settings().user_agent


@pytest.mark.asyncio
async def test_client_sends_the_user_agent_it_was_given() -> None:
    """The header must survive the trip from settings into the httpx client."""
    api = SejmApiClient(cache=TTLCache(max_entries=1), user_agent="law-scrapper-mcp/1.2.3 (+https://example.test)")
    await api.start()
    try:
        assert api._client is not None
        assert api._client.headers["User-Agent"] == "law-scrapper-mcp/1.2.3 (+https://example.test)"
    finally:
        await api.close()


def test_no_module_hardcodes_a_versioned_user_agent() -> None:
    """F52 regression guard: the version must live in exactly one place.

    The original defect was a literal `law-scrapper-mcp/2.0` in the client, which
    nobody noticed drifting because nothing tied it to `server_version`.
    """
    offenders = [
        f"{path.relative_to(CLIENT_PACKAGE)}:{number}"
        for path in CLIENT_PACKAGE.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if VERSIONED_LITERAL.search(line)
    ]
    assert offenders == []
