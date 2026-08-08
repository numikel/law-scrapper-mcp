"""Configuration settings for Law Scrapper MCP."""

from __future__ import annotations

import logging
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Widełki limitu długości wzorca (D3.1). Wartość spoza zakresu jest przycinana
# do najbliższej granicy, a nie odrzucana — start serwera nie może się nie udać.
MAX_PATTERN_LENGTH_FLOOR = 64
MAX_PATTERN_LENGTH_CEILING = 4096

# Dolna granica liczby rekordów, jakie filter_results może zwrócić. Wartość 0 (lub ujemna)
# oznacza, że KAŻDE wywołanie na niepustym zestawie wyników skończy się odmową —
# narzędzie stałoby się trwale niesprawne. Górnej granicy celowo nie ma: to pozostaje
# w gestii operatora, podobnie jak reszta pól tego pliku (bez Field()).
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

    # Filtrowanie wyników (filter_results)
    # Parametry operacyjne (D3): strojalne przez operatora. Nie są zabezpieczeniem
    # przed ReDoS — tę rolę pełni silnik RE2 w services/pattern_matching.py.
    max_pattern_length: int = 512
    filter_max_records: int = 100

    @property
    def effective_max_pattern_length(self) -> int:
        """Limit długości wzorca po przycięciu do widełek (D3.1)."""
        return min(
            max(self.max_pattern_length, MAX_PATTERN_LENGTH_FLOOR),
            MAX_PATTERN_LENGTH_CEILING,
        )

    @property
    def max_pattern_length_was_clamped(self) -> bool:
        """Czy skonfigurowana wartość musiała zostać przycięta do widełek."""
        return self.effective_max_pattern_length != self.max_pattern_length

    @property
    def effective_filter_max_records(self) -> int:
        """Limit rekordów po przycięciu do dolnej granicy.

        Asymetrycznie wobec `max_pattern_length` — bez osobnego ostrzeżenia w logu:
        wartość efektywna jest i tak obserwowalna w komunikacie ResultSetTooLargeError
        przy pierwszym wywołaniu filter_results, więc cichy floor nie ukrywa problemu.
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
    """Zgłoś w logu fakt przycięcia limitu długości wzorca (D3.1, punkt 1)."""
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
