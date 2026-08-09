"""Configuration settings for Law Scrapper MCP."""

from __future__ import annotations

import logging
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

MAX_PATTERN_LENGTH_FLOOR = 64
MAX_PATTERN_LENGTH_CEILING = 4096
FILTER_MAX_RECORDS_FLOOR = 1


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    model_config = SettingsConfigDict(env_prefix="LAW_MCP_")

    # Transport
    transport: str = "stdio"
    host: str = "0.0.0.0"
    port: int = 7683

    # API client
    api_timeout: float = 30.0
    api_max_concurrent: int = 10
    api_max_retries: int = 3

    # Cache TTL (seconds)
    cache_metadata_ttl: int = 86400
    cache_search_ttl: int = 600
    cache_browse_ttl: int = 3600
    cache_details_ttl: int = 3600
    cache_changes_ttl: int = 300
    cache_max_entries: int = 1000

    # Document Store
    doc_store_max_documents: int = 10
    doc_store_max_size_bytes: int = 5 * 1024 * 1024
    doc_store_ttl: int = 7200

    # Filtering
    max_pattern_length: int = 512
    filter_max_records: int = 100

    @property
    def effective_max_pattern_length(self) -> int:
        """Return max pattern length clamped to the allowed range (D3.1)."""
        return min(
            max(self.max_pattern_length, MAX_PATTERN_LENGTH_FLOOR),
            MAX_PATTERN_LENGTH_CEILING,
        )

    @property
    def max_pattern_length_was_clamped(self) -> bool:
        """Return whether the configured max pattern length was clamped."""
        return self.effective_max_pattern_length != self.max_pattern_length

    @property
    def effective_filter_max_records(self) -> int:
        """Return filter max records raised to the minimum allowed value.

        Asymmetric vs `max_pattern_length` — no separate log warning: the
        effective value is already visible in the ResultSetTooLargeError message
        on the first filter_results call, so a silent floor does not hide the issue.
        """
        return max(self.filter_max_records, FILTER_MAX_RECORDS_FLOOR)

    # Circuit breaker
    circuit_breaker_threshold: int = 5
    circuit_breaker_recovery_timeout: float = 60.0
    circuit_breaker_half_open_max_calls: int = 3

    # Logging
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"

    # Server info
    server_name: str = "law-scrapper-mcp"
    server_version: str = "2.4.0"


def log_pattern_limit_clamping(current: Settings, log: logging.Logger) -> None:
    """Log a warning when the max pattern length setting was clamped (D3.1)."""
    if not current.max_pattern_length_was_clamped:
        return
    log.warning(
        "LAW_MCP_MAX_PATTERN_LENGTH=%d jest poza dozwolonym zakresem %d-%d; serwer używa wartości efektywnej %d.",
        current.max_pattern_length,
        MAX_PATTERN_LENGTH_FLOOR,
        MAX_PATTERN_LENGTH_CEILING,
        current.effective_max_pattern_length,
    )


settings = Settings()
