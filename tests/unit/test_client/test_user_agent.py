"""Tests for the identity the client presents to upstream hosts (finding F52, FR-016)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import respx
from httpx import Response

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.egress import CallerIdentity, EgressDefaults, EgressHost, EgressHub
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.config import USER_AGENT_CONTACT, Settings

CLIENT_PACKAGE = Path(__file__).parents[3] / "src" / "law_scrapper_mcp"

# Matches a hardcoded "law-scrapper-mcp/<version>" literal — the exact shape that
# drifted two major versions behind the real one before F52 was fixed.
VERSIONED_LITERAL = re.compile(r"law-scrapper-mcp/\d")

# The full shape the API administrator sees: name, a three-part version, and a contact
# channel they can use instead of a ban — always a URI after the plus sign, so an e-mail
# appears as `mailto:`.
USER_AGENT_ON_THE_WIRE = re.compile(r"^law-scrapper-mcp/\d+\.\d+\.\d+ \(\+(?:https?://|mailto:)[^\s()\\]+\)$")


@pytest.fixture(autouse=True)
def no_ambient_contact(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator who followed the README has `LAW_MCP_CONTACT` exported; the defaults must not see it."""
    monkeypatch.delenv("LAW_MCP_CONTACT", raising=False)


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
    settings = Settings()
    assert settings.user_agent == f"law-scrapper-mcp/{settings.server_version} (+{USER_AGENT_CONTACT})"


def test_a_url_contact_replaces_the_default_and_is_written_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAW_MCP_CONTACT", "https://ops.example.org/contact")
    settings = Settings()

    assert settings.user_agent == f"law-scrapper-mcp/{settings.server_version} (+https://ops.example.org/contact)"
    assert USER_AGENT_CONTACT not in settings.user_agent


def test_an_email_contact_is_written_as_a_mailto_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAW_MCP_CONTACT", "ops@example.org")
    settings = Settings()

    assert settings.user_agent == f"law-scrapper-mcp/{settings.server_version} (+mailto:ops@example.org)"


@pytest.mark.parametrize("contact", ["https://ops.example.org/contact", "ops@example.org"])
def test_the_header_keeps_the_wire_shape_for_both_contact_forms(contact: str) -> None:
    assert USER_AGENT_ON_THE_WIRE.match(Settings(contact=contact).user_agent)


def test_the_contact_does_not_touch_the_server_name_or_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-016: only the contact is operator-settable through the new variable."""
    monkeypatch.setenv("LAW_MCP_CONTACT", "ops@example.org")
    settings = Settings()

    assert settings.server_name == "law-scrapper-mcp"
    assert settings.user_agent.startswith(f"{settings.server_name}/{settings.server_version} ")


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


@pytest.mark.asyncio
@respx.mock
async def test_the_outgoing_request_carries_the_settings_user_agent() -> None:
    """Criterion 12: the header must reach the wire, with the version we really run.

    The original F52 defect was not a missing header but a hardcoded version drifting
    away from the real one, so the assertion ties the sent value back to
    `settings.server_version` rather than to a literal.
    """
    settings = Settings()
    route = respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1").mock(
        return_value=Response(200, json={"ELI": "DU/2024/1"})
    )
    api = SejmApiClient(cache=TTLCache(max_entries=1), user_agent=settings.user_agent)
    await api.start()
    try:
        await api.get_json("acts/DU/2024/1")
    finally:
        await api.close()

    sent = route.calls.last.request.headers["User-Agent"]
    assert USER_AGENT_ON_THE_WIRE.match(sent), sent
    assert sent.startswith(f"law-scrapper-mcp/{settings.server_version} ")


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("contact", "on_the_wire"),
    [
        ("https://ops.example.org/contact", "(+https://ops.example.org/contact)"),
        ("ops@example.org", "(+mailto:ops@example.org)"),
    ],
)
async def test_the_sejm_and_a_module_host_both_send_the_operators_contact(contact: str, on_the_wire: str) -> None:
    """One setting reaches every host: the Sejm directly, a module through the hub's product token."""
    settings = Settings(contact=contact)
    sejm_route = respx.get("https://api.sejm.gov.pl/eli/acts/DU/2024/1").mock(return_value=Response(200, json={}))
    module_route = respx.get("https://alpha.example.test/x").mock(return_value=Response(200, json={}))
    hub = EgressHub(TTLCache(max_entries=10), EgressDefaults(user_agent=settings.user_agent))
    sejm = SejmApiClient(cache=TTLCache(max_entries=1), user_agent=settings.user_agent)
    hub.adopt(sejm)
    await sejm.start()
    module_client = hub.client_for(
        EgressHost(name="Alpha", base_url="https://alpha.example.test"), CallerIdentity("law-scrapper-alpha", "1.2.3")
    )
    try:
        await sejm.get_json("acts/DU/2024/1")
        await module_client.get_json("x")
    finally:
        await sejm.close()
        await hub.close()

    version = settings.server_version
    assert sejm_route.calls.last.request.headers["User-Agent"] == f"law-scrapper-mcp/{version} {on_the_wire}"
    assert (
        module_route.calls.last.request.headers["User-Agent"]
        == f"law-scrapper-mcp/{version} {on_the_wire} law-scrapper-alpha/1.2.3"
    )
