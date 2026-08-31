"""List settings read from the environment, in both accepted spellings.

Kept in its own file rather than folded into `test_auth_settings_validation.py`
on purpose. Every other unit test constructs `Settings(...)` in Python, which is
the right default — it isolates the validators from the host environment. But it
also means nothing exercised `EnvSettingsSource`, and that is exactly where the
documented deployment recipe was failing: pydantic-settings decodes complex
fields as JSON inside the source, before any validator on the model runs.
"""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from law_scrapper_mcp.config import Settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop every LAW_MCP_* variable so the host environment cannot steer a case."""
    for name in list(os.environ):
        if name.startswith("LAW_MCP_"):
            monkeypatch.delenv(name, raising=False)


def test_single_value_reads_as_a_one_item_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """The form README's reverse-proxy recipe uses."""
    monkeypatch.setenv("LAW_MCP_ALLOWED_HOSTS", "mcp.example.com:*")
    monkeypatch.setenv("LAW_MCP_AUTH_MODE", "bearer")
    monkeypatch.setenv("LAW_MCP_AUTH_TOKEN", "x" * 32)

    assert Settings().allowed_hosts == ["mcp.example.com:*"]


def test_comma_separated_values_read_as_a_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAW_MCP_TRUSTED_PROXIES", "10.0.0.0/8, 192.168.0.0/16")

    assert Settings().trusted_proxies == ["10.0.0.0/8", "192.168.0.0/16"]


def test_json_array_still_reads_as_a_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backwards compatibility: JSON was the only form that used to work."""
    monkeypatch.setenv("LAW_MCP_AUTH_ALGORITHMS", '["RS256", "ES256", "PS256"]')

    assert Settings().auth_algorithms == ["RS256", "ES256", "PS256"]


@pytest.mark.parametrize(
    "variable",
    [
        "LAW_MCP_ALLOWED_HOSTS",
        "LAW_MCP_ALLOWED_ORIGINS",
        "LAW_MCP_AUTH_REQUIRED_SCOPES",
        "LAW_MCP_AUTH_ALGORITHMS",
        "LAW_MCP_TRUSTED_PROXIES",
    ],
)
def test_every_list_setting_accepts_a_flat_value(monkeypatch: pytest.MonkeyPatch, variable: str) -> None:
    """One decoding rule, not four — a new list field must not reintroduce the trap."""
    monkeypatch.setenv(variable, "https://mcp.example.com")
    monkeypatch.setenv("LAW_MCP_AUTH_MODE", "bearer")
    monkeypatch.setenv("LAW_MCP_AUTH_TOKEN", "x" * 32)

    settings = Settings()

    field = variable.removeprefix("LAW_MCP_").lower()
    assert getattr(settings, field) == ["https://mcp.example.com"]


def test_bracketed_ipv6_is_not_mistaken_for_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bracketed IPv6 literal opens and closes like a JSON array.

    `[::1]:*` is this project's own default allowlist entry, so the JSON branch
    must not swallow it — and a lone `[::1]` in the proxy list is
    indistinguishable from an array by shape alone.
    """
    monkeypatch.setenv("LAW_MCP_ALLOWED_HOSTS", "[::1]:*, 127.0.0.1:*")
    monkeypatch.setenv("LAW_MCP_TRUSTED_PROXIES", "[::1]")

    settings = Settings()

    assert settings.allowed_hosts == ["[::1]:*", "127.0.0.1:*"]
    assert settings.trusted_proxies == ["[::1]"]


def test_the_shipped_default_allowlist_survives_a_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Copying the documented default into the environment must work."""
    from law_scrapper_mcp.config import LOOPBACK_ALLOWED_HOSTS

    monkeypatch.setenv("LAW_MCP_ALLOWED_HOSTS", ", ".join(LOOPBACK_ALLOWED_HOSTS))

    assert Settings().allowed_hosts == LOOPBACK_ALLOWED_HOSTS


def test_empty_value_reads_as_an_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAW_MCP_TRUSTED_PROXIES", "")

    assert Settings().trusted_proxies == []


def test_flat_value_reaches_the_security_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    """The point of the fix: the D6 rule now sees the value, in Polish.

    Before, a flat value died in the settings source with an English
    `SettingsError` naming `EnvSettingsSource`, so an operator widening the
    allowlist without authentication never learned why.
    """
    monkeypatch.setenv("LAW_MCP_ALLOWED_HOSTS", "mcp.example.com:*")
    monkeypatch.setenv("LAW_MCP_AUTH_MODE", "none")

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    message = str(exc_info.value)
    assert "LAW_MCP_ALLOWED_HOSTS" in message
    assert "mcp.example.com:*" in message
    assert "LAW_MCP_AUTH_MODE" in message
