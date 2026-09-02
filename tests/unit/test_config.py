"""Tests for configuration settings."""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from law_scrapper_mcp.config import (
    FILTER_MAX_RECORDS_FLOOR,
    MAX_PATTERN_LENGTH_CEILING,
    MAX_PATTERN_LENGTH_FLOOR,
    Settings,
    log_pattern_limit_clamping,
)


class TestSettingsDefaults:
    """Tests for default configuration values."""

    def test_transport_default(self):
        """Test default transport setting."""
        settings = Settings()
        assert settings.transport == "stdio"

    def test_supported_transports_are_accepted(self):
        """Both supported transports round-trip unchanged."""
        assert Settings(transport="stdio").transport == "stdio"
        assert Settings(transport="streamable-http").transport == "streamable-http"

    def test_removed_sse_transport_is_rejected(self):
        """A 2.x `sse` value must fail loudly instead of falling back to stdio."""
        with pytest.raises(ValidationError) as exc_info:
            Settings(transport="sse")  # type: ignore[arg-type]
        assert "'sse' został usunięty w wersji 3.0.0" in str(exc_info.value)

    def test_unknown_transport_is_rejected(self):
        """An unknown transport must not silently degrade to stdio."""
        with pytest.raises(ValidationError) as exc_info:
            Settings(transport="websocket")  # type: ignore[arg-type]
        assert "Nieobsługiwany transport 'websocket'" in str(exc_info.value)

    def test_host_and_port_defaults(self):
        """Test default host and port."""
        settings = Settings()
        assert settings.host == "127.0.0.1"
        assert settings.port == 7683

    def test_api_timeout_default(self):
        """Test default API timeout."""
        settings = Settings()
        assert settings.api_timeout == 30.0

    def test_api_concurrency_defaults(self):
        """Test default API concurrency settings."""
        settings = Settings()
        assert settings.api_max_concurrent == 8

    def test_dead_retry_knob_is_gone(self):
        """api_max_retries never reached the client; keeping it advertised a lie.

        The attempt count was hardcoded in the old tenacity decorator, so setting
        LAW_MCP_API_MAX_RETRIES changed nothing. Unknown prefixed env vars are
        ignored, so removing the field breaks nobody.
        """
        assert not hasattr(Settings(), "api_max_retries")

    def test_retry_loop_defaults(self):
        """Test default retry loop settings."""
        settings = Settings()
        assert settings.api_max_attempts == 3
        assert settings.api_retry_budget == pytest.approx(45.0)

    @pytest.mark.parametrize("value", ["0", "-1"])
    def test_api_max_attempts_below_one_is_rejected(self, monkeypatch, value):
        """A zero attempt budget would silently disable the retry loop.

        With ``range(1, 1)`` the loop body never runs, so the client raises
        ApiUnavailableError without ever contacting the API and without recording
        anything on the circuit breaker. Fail loudly at startup instead.
        """
        monkeypatch.setenv("LAW_MCP_API_MAX_ATTEMPTS", value)
        with pytest.raises(ValidationError):
            Settings()

    def test_api_concurrency_defaults_split_the_previous_budget(self):
        """Criterion 14: the split rebalances the peak, it does not raise it.

        `api_max_concurrent` narrows to the LIGHT class (JSON), and heavy document
        downloads get their own two slots — so a run of PDF fetches can never take the
        last slot a search would have used.
        """
        settings = Settings()
        assert settings.api_max_concurrent == 8
        assert settings.api_max_concurrent_content == 2
        assert settings.api_max_concurrent + settings.api_max_concurrent_content == 10

    def test_api_rate_defaults(self):
        """5 rps with a burst of ten: one fan-out of ten calls pays nothing, a second
        one within the same second is braked — which is the loop F27 guards against."""
        settings = Settings()
        assert settings.api_rate_per_second == pytest.approx(5.0)
        assert settings.api_rate_burst == 10

    @pytest.mark.parametrize("value", ["0", "-1"])
    def test_api_rate_per_second_must_be_positive(self, monkeypatch, value):
        """Criterion 3: a zero rate must fail at startup, not turn the client into a
        bucket that never refills and a server that never answers."""
        monkeypatch.setenv("LAW_MCP_API_RATE_PER_SECOND", value)
        with pytest.raises(ValidationError):
            Settings()

    @pytest.mark.parametrize("value", ["0", "-1"])
    def test_api_rate_burst_below_one_is_rejected(self, monkeypatch, value):
        """A bucket that cannot hold one whole token never admits a request."""
        monkeypatch.setenv("LAW_MCP_API_RATE_BURST", value)
        with pytest.raises(ValidationError):
            Settings()

    @pytest.mark.parametrize("value", ["0", "-1"])
    def test_api_max_concurrent_content_below_one_is_rejected(self, monkeypatch, value):
        """Zero heavy slots would deadlock every act-content download."""
        monkeypatch.setenv("LAW_MCP_API_MAX_CONCURRENT_CONTENT", value)
        with pytest.raises(ValidationError):
            Settings()

    def test_api_rate_settings_come_from_the_environment(self, monkeypatch):
        """The operator's one useful lever when the API admin asks us to slow down."""
        monkeypatch.setenv("LAW_MCP_API_RATE_PER_SECOND", "2.5")
        monkeypatch.setenv("LAW_MCP_API_RATE_BURST", "4")
        monkeypatch.setenv("LAW_MCP_API_MAX_CONCURRENT_CONTENT", "1")

        settings = Settings()

        assert settings.api_rate_per_second == pytest.approx(2.5)
        assert settings.api_rate_burst == 4
        assert settings.api_max_concurrent_content == 1

    @pytest.mark.parametrize("value", ["0", "-0.5"])
    def test_api_retry_budget_must_be_positive(self, monkeypatch, value):
        """A non-positive budget makes every planned wait exceed the deadline."""
        monkeypatch.setenv("LAW_MCP_API_RETRY_BUDGET", value)
        with pytest.raises(ValidationError):
            Settings()

    def test_cache_ttl_defaults(self):
        """Test default cache TTL values."""
        settings = Settings()
        assert settings.cache_metadata_ttl == 86400  # 24 hours
        assert settings.cache_search_ttl == 600  # 10 minutes
        assert settings.cache_browse_ttl == 3600  # 1 hour
        assert settings.cache_details_ttl == 3600  # 1 hour
        assert settings.cache_changes_ttl == 300  # 5 minutes
        assert settings.cache_max_entries == 1000

    def test_document_store_defaults(self):
        """Test default document store settings."""
        settings = Settings()
        assert settings.doc_store_max_documents == 10
        assert settings.doc_store_max_size_bytes == 5 * 1024 * 1024  # 5 MB
        assert settings.doc_store_ttl == 7200  # 2 hours

    def test_logging_defaults(self):
        """Test default logging settings."""
        settings = Settings()
        assert settings.log_level == "INFO"
        assert settings.log_format == "text"

    def test_server_info_defaults(self):
        """Test default server info."""
        settings = Settings()
        assert settings.server_name == "law-scrapper-mcp"
        assert settings.server_version == "4.1.0"


class TestSettingsFromEnvironment:
    """Tests for loading settings from environment variables."""

    def test_transport_from_env(self, monkeypatch):
        """Test loading transport from environment variable."""
        monkeypatch.setenv("LAW_MCP_TRANSPORT", "streamable-http")
        settings = Settings()
        assert settings.transport == "streamable-http"

    def test_port_from_env(self, monkeypatch):
        """Test loading port from environment variable."""
        monkeypatch.setenv("LAW_MCP_PORT", "9000")
        settings = Settings()
        assert settings.port == 9000

    def test_api_timeout_from_env(self, monkeypatch):
        """Test loading API timeout from environment variable."""
        monkeypatch.setenv("LAW_MCP_API_TIMEOUT", "60.0")
        settings = Settings()
        assert settings.api_timeout == 60.0

    def test_cache_ttl_from_env(self, monkeypatch):
        """Test loading cache TTL from environment variable."""
        monkeypatch.setenv("LAW_MCP_CACHE_SEARCH_TTL", "1200")
        settings = Settings()
        assert settings.cache_search_ttl == 1200

    def test_doc_store_from_env(self, monkeypatch):
        """Test loading document store settings from environment."""
        monkeypatch.setenv("LAW_MCP_DOC_STORE_MAX_DOCUMENTS", "20")
        monkeypatch.setenv("LAW_MCP_DOC_STORE_TTL", "3600")
        settings = Settings()
        assert settings.doc_store_max_documents == 20
        assert settings.doc_store_ttl == 3600

    def test_log_level_from_env(self, monkeypatch):
        """Test loading log level from environment variable."""
        monkeypatch.setenv("LAW_MCP_LOG_LEVEL", "DEBUG")
        settings = Settings()
        assert settings.log_level == "DEBUG"

    def test_multiple_env_vars(self, monkeypatch):
        """Test loading multiple settings from environment variables."""
        monkeypatch.setenv("LAW_MCP_TRANSPORT", "streamable-http")
        monkeypatch.setenv("LAW_MCP_PORT", "7683")
        monkeypatch.setenv("LAW_MCP_API_TIMEOUT", "45.0")
        monkeypatch.setenv("LAW_MCP_LOG_LEVEL", "WARNING")

        settings = Settings()
        assert settings.transport == "streamable-http"
        assert settings.port == 7683
        assert settings.api_timeout == 45.0
        assert settings.log_level == "WARNING"

    def test_env_prefix_required(self, monkeypatch):
        """Test that env vars without LAW_MCP_ prefix are ignored."""
        monkeypatch.setenv("TRANSPORT", "http")  # Wrong prefix
        monkeypatch.setenv("PORT", "9000")  # Wrong prefix

        settings = Settings()
        # Should use defaults, not env vars without prefix
        assert settings.transport == "stdio"
        assert settings.port == 7683


class TestSettingsValidation:
    """Tests for settings validation."""

    def test_invalid_port_type(self, monkeypatch):
        """Test that invalid port type raises error."""
        monkeypatch.setenv("LAW_MCP_PORT", "invalid")
        with pytest.raises((ValueError, TypeError)):
            Settings()

    def test_invalid_timeout_type(self, monkeypatch):
        """Test that invalid timeout type raises error."""
        monkeypatch.setenv("LAW_MCP_API_TIMEOUT", "invalid")
        with pytest.raises((ValueError, TypeError)):
            Settings()

    def test_invalid_ttl_type(self, monkeypatch):
        """Test that invalid TTL type raises error."""
        monkeypatch.setenv("LAW_MCP_CACHE_SEARCH_TTL", "invalid")
        with pytest.raises((ValueError, TypeError)):
            Settings()


class TestPatternFilterSettings:
    """Tests for result-filtering settings"""

    def test_defaults(self):
        settings = Settings()
        assert settings.max_pattern_length == 512
        assert settings.filter_max_records == 100
        assert settings.effective_max_pattern_length == 512
        assert settings.max_pattern_length_was_clamped is False

    def test_value_within_range_is_used_as_is(self, monkeypatch):
        monkeypatch.setenv("LAW_MCP_MAX_PATTERN_LENGTH", "1024")
        settings = Settings()
        assert settings.effective_max_pattern_length == 1024
        assert settings.max_pattern_length_was_clamped is False

    def test_value_above_ceiling_is_clamped_down(self, monkeypatch):
        monkeypatch.setenv("LAW_MCP_MAX_PATTERN_LENGTH", "10000")
        settings = Settings()
        assert settings.max_pattern_length == 10000
        assert settings.effective_max_pattern_length == MAX_PATTERN_LENGTH_CEILING == 4096
        assert settings.max_pattern_length_was_clamped is True

    def test_value_below_floor_is_clamped_up(self, monkeypatch):
        monkeypatch.setenv("LAW_MCP_MAX_PATTERN_LENGTH", "8")
        settings = Settings()
        assert settings.effective_max_pattern_length == MAX_PATTERN_LENGTH_FLOOR == 64
        assert settings.max_pattern_length_was_clamped is True

    def test_out_of_range_value_does_not_block_startup(self, monkeypatch):
        """Out-of-range configuration must not abort server startup"""
        monkeypatch.setenv("LAW_MCP_MAX_PATTERN_LENGTH", "10000")
        settings = Settings()  # must not raise
        assert settings.effective_max_pattern_length == 4096

    def test_filter_max_records_from_env(self, monkeypatch):
        monkeypatch.setenv("LAW_MCP_FILTER_MAX_RECORDS", "250")
        settings = Settings()
        assert settings.filter_max_records == 250

    @pytest.mark.parametrize("value", [MAX_PATTERN_LENGTH_FLOOR, MAX_PATTERN_LENGTH_CEILING])
    def test_value_exactly_on_boundary_is_not_clamped(self, monkeypatch, value):
        monkeypatch.setenv("LAW_MCP_MAX_PATTERN_LENGTH", str(value))
        settings = Settings()
        assert settings.effective_max_pattern_length == value
        assert settings.max_pattern_length_was_clamped is False

    def test_filter_max_records_zero_is_clamped_to_floor(self, monkeypatch):
        monkeypatch.setenv("LAW_MCP_FILTER_MAX_RECORDS", "0")
        settings = Settings()
        assert settings.filter_max_records == 0
        assert settings.effective_filter_max_records == FILTER_MAX_RECORDS_FLOOR == 1

    def test_filter_max_records_negative_is_clamped_to_floor(self, monkeypatch):
        monkeypatch.setenv("LAW_MCP_FILTER_MAX_RECORDS", "-5")
        settings = Settings()
        assert settings.filter_max_records == -5
        assert settings.effective_filter_max_records == 1

    def test_filter_max_records_within_range_is_used_as_is(self, monkeypatch):
        monkeypatch.setenv("LAW_MCP_FILTER_MAX_RECORDS", "250")
        settings = Settings()
        assert settings.filter_max_records == 250
        assert settings.effective_filter_max_records == 250


class TestPatternLimitClampingLog:
    def test_warning_lists_configured_and_effective_values(self, monkeypatch, caplog):
        monkeypatch.setenv("LAW_MCP_MAX_PATTERN_LENGTH", "10000")
        settings = Settings()
        log = logging.getLogger("test_clamping")

        with caplog.at_level(logging.WARNING, logger="test_clamping"):
            log_pattern_limit_clamping(settings, log)

        assert "10000" in caplog.text
        assert "wartości efektywnej 4096" in caplog.text

    def test_no_warning_when_value_within_range(self, caplog):
        settings = Settings()
        log = logging.getLogger("test_clamping")

        with caplog.at_level(logging.WARNING, logger="test_clamping"):
            log_pattern_limit_clamping(settings, log)

        assert caplog.text == ""


class TestShutdownGrace:
    """Tests for graceful shutdown window configuration."""

    def test_shutdown_grace_defaults_to_fifteen_seconds(self) -> None:
        """The audit's recommended window, adopted verbatim as the default."""
        assert Settings().shutdown_grace == 15.0

    def test_shutdown_grace_reads_the_environment(self, monkeypatch) -> None:
        monkeypatch.setenv("LAW_MCP_SHUTDOWN_GRACE", "25")

        assert Settings().shutdown_grace == 25.0

    @pytest.mark.parametrize("value", ["0", "-1"])
    def test_shutdown_grace_rejects_non_positive_values(self, monkeypatch, value) -> None:
        """A zero or negative window would mean 'kill in-flight requests immediately'.

        Failing at startup beats discovering it during a deploy.
        """
        monkeypatch.setenv("LAW_MCP_SHUTDOWN_GRACE", value)

        with pytest.raises(ValidationError):
            Settings()
