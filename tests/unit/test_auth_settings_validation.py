"""Startup validation binding network exposure to the authentication mode."""

from __future__ import annotations

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
    for name in list(__import__("os").environ):
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


def test_secret_is_not_in_the_repr() -> None:
    """SecretStr keeps the token out of crash dumps and config logging."""
    current = Settings(auth_mode="bearer", auth_token=VALID_TOKEN)
    assert VALID_TOKEN not in repr(current)


def test_token_does_not_leak_into_validation_errors(tmp_path: Path) -> None:
    """Pydantic must not expose the raw token in ValidationError messages."""
    token_file = tmp_path / "token"
    token_file.write_text(VALID_TOKEN, encoding="utf-8")
    with pytest.raises(ValidationError) as exc_info:
        Settings(auth_mode="bearer", auth_token=VALID_TOKEN, auth_token_file=token_file)
    assert VALID_TOKEN not in str(exc_info.value)


def test_rate_limit_defaults_follow_d17() -> None:
    current = Settings()
    assert (current.rate_limit_enabled, current.rate_limit_requests) == (True, 60)
    assert (current.rate_limit_window, current.rate_limit_burst) == (60.0, 10)
    assert current.trusted_proxies == []


class TestRemoteBindWarning:
    """Criterion 6 (D7): a visible signal instead of silent exposure."""

    def test_loopback_bind_logs_nothing(self, caplog: pytest.LogCaptureFixture) -> None:
        from law_scrapper_mcp.config import log_remote_bind_warning

        current = Settings(transport="streamable-http")
        with caplog.at_level("WARNING"):
            log_remote_bind_warning(current, __import__("logging").getLogger("test"))
        assert caplog.records == []

    def test_remote_bind_logs_one_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        from law_scrapper_mcp.config import log_remote_bind_warning

        current = Settings(transport="streamable-http", host="0.0.0.0", auth_mode="bearer", auth_token=VALID_TOKEN)
        with caplog.at_level("WARNING"):
            log_remote_bind_warning(current, __import__("logging").getLogger("test"))
        assert len(caplog.records) == 1
        assert "0.0.0.0" in caplog.records[0].getMessage()

    def test_stdio_transport_never_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """The bind address is meaningless without an HTTP listener."""
        from law_scrapper_mcp.config import log_remote_bind_warning

        current = Settings(transport="stdio", host="0.0.0.0", auth_mode="bearer", auth_token=VALID_TOKEN)
        with caplog.at_level("WARNING"):
            log_remote_bind_warning(current, __import__("logging").getLogger("test"))
        assert caplog.records == []


def test_config_contains_no_wildcard_bind() -> None:
    """Criterion 13: the wildcard bind must not survive anywhere in config.py."""
    from pathlib import Path as _Path

    source = (_Path(__file__).parents[2] / "src/law_scrapper_mcp/config.py").read_text(encoding="utf-8")
    assert "0.0.0.0" not in source
