"""Startup validation binding network exposure to the authentication mode."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from law_scrapper_mcp.config import MIN_AUTH_TOKEN_BYTES, Settings

VALID_TOKEN = "x" * MIN_AUTH_TOKEN_BYTES


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop every LAW_MCP_* variable so the host environment cannot steer a case.

    pydantic-settings reads os.environ at construction time, so a developer with
    LAW_MCP_AUTH_MODE exported would otherwise see failures no one can reproduce.
    """
    for name in list(os.environ):
        if name.startswith("LAW_MCP_"):
            monkeypatch.delenv(name, raising=False)


def test_remote_bind_without_auth_is_rejected() -> None:
    """Criterion 1 (D5): exposure without authentication must not start."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(transport="streamable-http", host="0.0.0.0", auth_mode="none")
    assert "LAW_MCP_AUTH_MODE" in str(exc_info.value)


def test_remote_bind_with_auth_is_accepted() -> None:
    current = Settings(transport="streamable-http", host="0.0.0.0", auth_mode="bearer", auth_token=VALID_TOKEN)
    assert current.host == "0.0.0.0"


def test_loopback_bind_without_auth_is_accepted() -> None:
    """The local path must stay frictionless — that is the whole point of D1."""
    assert Settings(transport="streamable-http", auth_mode="none").host == "127.0.0.1"


def test_remote_allowlist_without_auth_is_rejected() -> None:
    """Criterion 2 (D6): widening the allowlist without a token is impossible."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(auth_mode="none", allowed_hosts=["mcp.example.com:443"])
    assert "LAW_MCP_ALLOWED_HOSTS" in str(exc_info.value)


def test_remote_origin_without_auth_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(auth_mode="none", allowed_origins=["https://mcp.example.com"])
    assert "LAW_MCP_ALLOWED_ORIGINS" in str(exc_info.value)


def test_bearer_without_any_token_source_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(auth_mode="bearer")
    assert "LAW_MCP_AUTH_TOKEN" in str(exc_info.value)


def test_both_token_sources_are_rejected(tmp_path: Path) -> None:
    """Criterion 3 (D8, A8): silent precedence would hide a swapped secret."""
    token_file = tmp_path / "token"
    token_file.write_text(VALID_TOKEN, encoding="utf-8")
    with pytest.raises(ValidationError) as exc_info:
        Settings(auth_mode="bearer", auth_token=VALID_TOKEN, auth_token_file=token_file)
    assert "jednocześnie" in str(exc_info.value)


def test_short_token_is_rejected() -> None:
    """Criterion 4 (D8): length only — entropy is not, and cannot be, measured."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(auth_mode="bearer", auth_token="za-krotki")
    assert str(MIN_AUTH_TOKEN_BYTES) in str(exc_info.value)


def test_token_one_byte_short_of_the_minimum_is_rejected() -> None:
    """Off-by-one guard: MIN_AUTH_TOKEN_BYTES - 1 bytes must still fail."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(auth_mode="bearer", auth_token="x" * (MIN_AUTH_TOKEN_BYTES - 1))
    assert str(MIN_AUTH_TOKEN_BYTES) in str(exc_info.value)


def test_token_file_is_stripped_before_the_length_check(tmp_path: Path) -> None:
    """A secret file almost always ends with a newline."""
    token_file = tmp_path / "token"
    token_file.write_text(f"{VALID_TOKEN}\n", encoding="utf-8")
    current = Settings(auth_mode="bearer", auth_token_file=token_file)
    assert current.resolve_auth_token() == VALID_TOKEN


def test_missing_token_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(auth_mode="bearer", auth_token_file=tmp_path / "nie-ma")
    assert "LAW_MCP_AUTH_TOKEN_FILE" in str(exc_info.value)


def test_binary_token_file_is_rejected(tmp_path: Path) -> None:
    """A non-UTF-8 token file must raise ValidationError, not a raw UnicodeDecodeError."""
    token_file = tmp_path / "token"
    token_file.write_bytes(b"\xff\xfe\x00\x01")
    with pytest.raises(ValidationError) as exc_info:
        Settings(auth_mode="bearer", auth_token_file=token_file)
    assert "LAW_MCP_AUTH_TOKEN_FILE" in str(exc_info.value)


def test_invalid_trusted_proxy_cidr_is_rejected() -> None:
    """Malformed CIDR entries must fail at Settings construction, not on first request."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(trusted_proxies=["nie-cidr"])
    message = str(exc_info.value)
    assert "LAW_MCP_TRUSTED_PROXIES" in message
    assert "nie-cidr" in message


def test_bracketed_ipv6_trusted_proxy_is_rejected() -> None:
    """`[::1]:*` is valid in LAW_MCP_ALLOWED_HOSTS, so writing `[::1]` here by
    analogy is the obvious mistake — but bracketed notation is an HTTP host
    convention, never valid CIDR syntax, and trusted_proxies must be `::1`."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(trusted_proxies=["[::1]"])
    message = str(exc_info.value)
    assert "LAW_MCP_TRUSTED_PROXIES" in message
    assert "[::1]" in message


def test_valid_trusted_proxy_cidr_is_accepted() -> None:
    current = Settings(trusted_proxies=["10.0.0.0/8", "192.168.1.1"])
    assert current.trusted_proxies == ["10.0.0.0/8", "192.168.1.1"]


def test_oauth_without_issuer_is_rejected() -> None:
    """Criterion 5 (D2, A5): no silent downgrade to the shared-secret mode."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            auth_mode="oauth",
            auth_audience="api://law-scrapper",
            auth_resource_server_url="https://mcp.example.com/mcp",
        )
    message = str(exc_info.value)
    assert "LAW_MCP_AUTH_ISSUER" in message
    assert "bearer" not in message.lower().replace("law_mcp_auth_mode", "")


def test_oauth_with_full_configuration_is_accepted() -> None:
    current = Settings(
        auth_mode="oauth",
        auth_issuer="https://login.microsoftonline.com/tenant/v2.0",
        auth_audience="api://law-scrapper",
        auth_resource_server_url="https://mcp.example.com/mcp",
    )
    assert current.auth_algorithms == ["RS256", "ES256"]
    assert current.auth_jwks_cache_ttl == 3600


def test_oauth_http_issuer_is_rejected() -> None:
    """`AnyHttpUrl` accepts plain `http://` as readily as `https://`; the
    discovery request built from `auth_issuer` would otherwise go out over an
    unauthenticated channel an attacker could MITM to substitute a malicious
    discovery document."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            auth_mode="oauth",
            auth_issuer="http://login.microsoftonline.com/tenant/v2.0",
            auth_audience="api://law-scrapper",
            auth_resource_server_url="https://mcp.example.com/mcp",
        )
    message = str(exc_info.value)
    assert "LAW_MCP_AUTH_ISSUER" in message
    assert "https" in message


def test_oauth_http_jwks_uri_is_rejected() -> None:
    """A directly-configured `auth_jwks_uri` skips `_discover_jwks_uri`
    entirely, so its own https-only discovery check never runs — this must be
    enforced independently, or a plain-http `LAW_MCP_AUTH_JWKS_URI` bypasses
    the downgrade protection completely."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            auth_mode="oauth",
            auth_issuer="https://login.microsoftonline.com/tenant/v2.0",
            auth_jwks_uri="http://login.microsoftonline.com/tenant/discovery/keys",
            auth_audience="api://law-scrapper",
            auth_resource_server_url="https://mcp.example.com/mcp",
        )
    message = str(exc_info.value)
    assert "LAW_MCP_AUTH_JWKS_URI" in message
    assert "https" in message


def test_secret_is_not_in_the_repr() -> None:
    """SecretStr keeps the token out of crash dumps and config logging."""
    current = Settings(auth_mode="bearer", auth_token=VALID_TOKEN)
    assert VALID_TOKEN not in repr(current)


def test_token_does_not_leak_into_validation_errors() -> None:
    """Pydantic must not expose the raw token in ValidationError messages.

    This uses oauth mode (requires issuer/audience/resource_server_url) with a
    bearer token present, so pydantic's input_value dict is small (≤2 kwargs)
    and doesn't get truncated. Without hide_input_in_errors=True, the plaintext
    token would be visible in the error output.
    """
    with pytest.raises(ValidationError) as exc_info:
        Settings(auth_mode="oauth", auth_token=VALID_TOKEN)
    assert VALID_TOKEN not in str(exc_info.value)


def test_rate_limit_defaults_follow_d17() -> None:
    current = Settings()
    assert (current.rate_limit_enabled, current.rate_limit_requests) == (True, 60)
    assert (current.rate_limit_window, current.rate_limit_burst) == (60.0, 10)
    assert current.trusted_proxies == []


def test_rate_limit_requests_zero_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(rate_limit_requests=0)


def test_rate_limit_window_zero_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(rate_limit_window=0)


class TestRemoteBindWarning:
    """Criterion 6 (D7): a visible signal instead of silent exposure."""

    def test_loopback_bind_logs_nothing(self, caplog: pytest.LogCaptureFixture) -> None:
        from law_scrapper_mcp.config import log_remote_bind_warning

        current = Settings(transport="streamable-http")
        with caplog.at_level("WARNING"):
            log_remote_bind_warning(current, logging.getLogger("test"))
        assert [r for r in caplog.records if r.name == "test"] == []

    def test_remote_bind_logs_one_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        from law_scrapper_mcp.config import log_remote_bind_warning

        current = Settings(transport="streamable-http", host="0.0.0.0", auth_mode="bearer", auth_token=VALID_TOKEN)
        with caplog.at_level("WARNING"):
            log_remote_bind_warning(current, logging.getLogger("test"))
        records = [r for r in caplog.records if r.name == "test"]
        assert len(records) == 1
        assert "0.0.0.0" in records[0].getMessage()

    def test_stdio_transport_never_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """The bind address is meaningless without an HTTP listener."""
        from law_scrapper_mcp.config import log_remote_bind_warning

        current = Settings(transport="stdio", host="0.0.0.0", auth_mode="bearer", auth_token=VALID_TOKEN)
        with caplog.at_level("WARNING"):
            log_remote_bind_warning(current, logging.getLogger("test"))
        assert [r for r in caplog.records if r.name == "test"] == []


def test_config_contains_no_wildcard_bind() -> None:
    """Criterion 13: the wildcard bind must not survive anywhere in config.py.

    The security-boundary validator that used to enforce this at runtime now
    lives in `config_validation.py` (with shared helpers in
    `config_primitives.py`), so the source-scan must cover all three modules
    the split produced, not just the one that kept the `Settings` class.
    """
    from pathlib import Path as _Path

    src = _Path(__file__).parents[2] / "src/law_scrapper_mcp"
    for module in ("config.py", "config_validation.py", "config_primitives.py"):
        source = (src / module).read_text(encoding="utf-8")
        assert "0.0.0.0" not in source, module
