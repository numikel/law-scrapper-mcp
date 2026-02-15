"""Configuration settings for Law Scrapper MCP."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Circuit breaker
    circuit_breaker_threshold: int = 5
    circuit_breaker_recovery_timeout: float = 60.0
    circuit_breaker_half_open_max_calls: int = 3

    # Logging
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"

    # Server info
    server_name: str = "law-scrapper-mcp"
    server_version: str = "2.3.0"


settings = Settings()
