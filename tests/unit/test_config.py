"""Tests for configuration settings."""

from __future__ import annotations

import pytest

from law_scrapper_mcp.config import Settings


class TestSettingsDefaults:
    """Tests for default configuration values."""

    def test_transport_default(self):
        """Test default transport setting."""
        settings = Settings()
        assert settings.transport == "stdio"

    def test_host_and_port_defaults(self):
        """Test default host and port."""
        settings = Settings()
        assert settings.host == "0.0.0.0"
        assert settings.port == 7683

    def test_api_timeout_default(self):
        """Test default API timeout."""
        settings = Settings()
        assert settings.api_timeout == 30.0

    def test_api_concurrency_defaults(self):
        """Test default API concurrency settings."""
        settings = Settings()
        assert settings.api_max_concurrent == 10
        assert settings.api_max_retries == 3

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
        assert settings.server_version == "2.3.0"


class TestSettingsFromEnvironment:
    """Tests for loading settings from environment variables."""

    def test_transport_from_env(self, monkeypatch):
        """Test loading transport from environment variable."""
        monkeypatch.setenv("LAW_MCP_TRANSPORT", "sse")
        settings = Settings()
        assert settings.transport == "sse"

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
        monkeypatch.setenv("LAW_MCP_TRANSPORT", "sse")
        monkeypatch.setenv("LAW_MCP_PORT", "7683")
        monkeypatch.setenv("LAW_MCP_API_TIMEOUT", "45.0")
        monkeypatch.setenv("LAW_MCP_LOG_LEVEL", "WARNING")

        settings = Settings()
        assert settings.transport == "sse"
        assert settings.port == 7683
        assert settings.api_timeout == 45.0
        assert settings.log_level == "WARNING"

    def test_env_prefix_required(self, monkeypatch):
        """Test that env vars without LAW_MCP_ prefix are ignored."""
        monkeypatch.setenv("TRANSPORT", "sse")  # Wrong prefix
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
