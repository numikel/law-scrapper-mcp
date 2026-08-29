"""Configuration settings for Law Scrapper MCP."""

from __future__ import annotations

import logging
from ipaddress import ip_address
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MAX_PATTERN_LENGTH_FLOOR = 64
MAX_PATTERN_LENGTH_CEILING = 4096
FILTER_MAX_RECORDS_FLOOR = 1

# Contact channel advertised to api.sejm.gov.pl. The API is run by a state
# institution, so its administrator needs a way to reach us that is not a ban.
USER_AGENT_CONTACT = "https://github.com/numikel/law-scrapper-mcp"

MIN_AUTH_TOKEN_BYTES = 32

LOOPBACK_ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
LOOPBACK_ALLOWED_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]


def _host_of(entry: str) -> str:
    """Strip scheme, port and brackets from an allowlist entry."""
    value = entry.strip()
    if "://" in value:
        value = value.split("://", 1)[1]
    if value.startswith("["):
        return value[1 : value.index("]")] if "]" in value else value[1:]
    # Bare IPv6 (contains multiple colons but no brackets): return unchanged
    if value.count(":") > 1:
        return value
    return value.split(":")[0]


def is_loopback_entry(entry: str) -> bool:
    """Whether an allowlist entry or bind address stays inside the loopback."""
    host = _host_of(entry).lower()
    if host in {"localhost", "::1"}:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    model_config = SettingsConfigDict(env_prefix="LAW_MCP_", hide_input_in_errors=True)

    # Transport
    transport: Literal["stdio", "streamable-http"] = "stdio"
    # Loopback by default (D7). This is a safe bind, not an access-control
    # mechanism — exposure is a deliberate act requiring an authentication mode.
    host: str = "127.0.0.1"
    port: int = 7683

    @field_validator("transport", mode="before")
    @classmethod
    def _reject_unsupported_transport(cls, value: object) -> object:
        """Fail loudly instead of silently falling back to stdio."""
        if value in ("stdio", "streamable-http"):
            return value
        if value == "sse":
            raise ValueError(
                "Transport 'sse' został usunięty w wersji 3.0.0. "
                "Ustaw LAW_MCP_TRANSPORT na 'stdio' albo 'streamable-http'."
            )
        raise ValueError(f"Nieobsługiwany transport '{value}'. Dozwolone wartości: 'stdio', 'streamable-http'.")

    def resolve_auth_token(self) -> str:
        """Return the bearer secret from whichever source is configured.

        Read on demand rather than cached on the model: the validator needs it
        before the model exists, and `build_auth()` needs it exactly once more.
        """
        if self.auth_token is not None:
            return self.auth_token.get_secret_value().strip()
        if self.auth_token_file is not None:
            return self.auth_token_file.read_text(encoding="utf-8").strip()
        return ""

    @model_validator(mode="after")
    def _enforce_security_boundary(self) -> Settings:
        if self.auth_mode == "bearer":
            if self.auth_token is not None and self.auth_token_file is not None:
                raise ValueError(
                    "LAW_MCP_AUTH_TOKEN i LAW_MCP_AUTH_TOKEN_FILE ustawione jednocześnie. "
                    "Wybierz jedno źródło tokenu — milcząca precedencja ukryłaby podmianę sekretu."
                )
            if self.auth_token is None and self.auth_token_file is None:
                raise ValueError("Tryb 'bearer' wymaga tokenu. Ustaw LAW_MCP_AUTH_TOKEN albo LAW_MCP_AUTH_TOKEN_FILE.")
            try:
                token = self.resolve_auth_token()
            except OSError as error:
                raise ValueError(
                    f"Nie udało się odczytać LAW_MCP_AUTH_TOKEN_FILE ({self.auth_token_file}): {error}"
                ) from error
            if len(token.encode("utf-8")) < MIN_AUTH_TOKEN_BYTES:
                raise ValueError(
                    f"Token uwierzytelniający musi mieć co najmniej {MIN_AUTH_TOKEN_BYTES} bajtów UTF-8. "
                    "Wygeneruj go poleceniem: openssl rand -base64 32"
                )

        if self.auth_mode == "oauth":
            required = (
                ("LAW_MCP_AUTH_ISSUER", self.auth_issuer),
                ("LAW_MCP_AUTH_AUDIENCE", self.auth_audience),
                ("LAW_MCP_AUTH_RESOURCE_SERVER_URL", self.auth_resource_server_url),
            )
            missing = [name for name, value in required if value is None]
            if missing:
                raise ValueError(
                    f"Tryb 'oauth' wymaga zmiennych: {', '.join(missing)}. "
                    "Serwer nie uruchomi się z niepełną konfiguracją OAuth."
                )

        if self.auth_mode == "none":
            if self.transport == "streamable-http" and not is_loopback_entry(self.host):
                raise ValueError(
                    f"Bind '{self.host}' wykracza poza pętlę zwrotną przy wyłączonym uwierzytelnianiu. "
                    "Ustaw LAW_MCP_AUTH_MODE na 'bearer' albo 'oauth', albo binduj na 127.0.0.1."
                )
            for name, entries in (
                ("LAW_MCP_ALLOWED_HOSTS", self.allowed_hosts),
                ("LAW_MCP_ALLOWED_ORIGINS", self.allowed_origins),
            ):
                remote = [entry for entry in entries if not is_loopback_entry(entry)]
                if remote:
                    raise ValueError(
                        f"{name} zawiera wpisy spoza pętli zwrotnej ({', '.join(remote)}) "
                        "przy wyłączonym uwierzytelnianiu. Ustaw LAW_MCP_AUTH_MODE."
                    )

        return self

    # Graceful shutdown window handed to uvicorn as `timeout_graceful_shutdown`.
    # Shorter than `api_timeout` on purpose: the trade-off between restart speed
    # and the share of requests allowed to finish is settled in favour of a fast
    # restart. Deployments that want it the other way round raise the value.
    # Constrained to ≥1 so the downstream `int()` cast in the bootstrap layer
    # never collapses the value to zero. The deployment contract is
    # `stop_grace_period >= 2 * shutdown_grace` — see docker-compose.yml.
    shutdown_grace: float = Field(default=15.0, ge=1)

    # Network boundary (F18). The defaults reproduce, value for value, the
    # constant that used to be hardcoded in server.py:43-47 — introducing the
    # fields changes nothing but the ability to override them.
    allowed_hosts: list[str] = Field(default_factory=lambda: list(LOOPBACK_ALLOWED_HOSTS))
    allowed_origins: list[str] = Field(default_factory=lambda: list(LOOPBACK_ALLOWED_ORIGINS))

    # Authentication (F17)
    auth_mode: Literal["none", "bearer", "oauth"] = "none"
    auth_token: SecretStr | None = None
    auth_token_file: Path | None = None
    auth_issuer: AnyHttpUrl | None = None
    auth_audience: str | None = None
    auth_jwks_uri: AnyHttpUrl | None = None
    auth_resource_server_url: AnyHttpUrl | None = None
    auth_required_scopes: list[str] = Field(default_factory=list)
    # An allowlist handed to the decoder, never read from the token header:
    # a decoder honouring `alg` accepts `none`, or HS256 verified with the
    # public JWKS key the attacker already has.
    auth_algorithms: list[str] = Field(default_factory=lambda: ["RS256", "ES256"])
    auth_jwks_cache_ttl: int = 3600

    # Inbound rate limiting (F26). Outbound throttling towards api.sejm.gov.pl
    # is a different budget in a different layer — see cluster 8.
    rate_limit_enabled: bool = True
    rate_limit_requests: int = Field(default=60, ge=1)
    rate_limit_window: float = Field(default=60.0, gt=0)
    rate_limit_burst: int = Field(default=10, ge=1)
    trusted_proxies: list[str] = Field(default_factory=list)

    # API client
    api_timeout: float = 30.0
    api_max_concurrent: int = 10
    # Bounded so that a misconfigured value degrades loudly at startup instead of
    # silently turning the retry loop into zero attempts.
    api_max_attempts: int = Field(default=3, ge=1)
    api_retry_budget: float = Field(default=45.0, gt=0)

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
        """Return max pattern length clamped to the allowed range"""
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
    server_version: str = "3.1.2"

    @property
    def user_agent(self) -> str:
        """Identity sent to api.sejm.gov.pl on every request.

        Derived rather than written out, so the header cannot drift away from
        the version the server actually reports — which is exactly what happened
        while it was a literal in the client.
        """
        return f"{self.server_name}/{self.server_version} (+{USER_AGENT_CONTACT})"


def log_pattern_limit_clamping(current: Settings, log: logging.Logger) -> None:
    """Log a warning when the max pattern length setting was clamped"""
    if not current.max_pattern_length_was_clamped:
        return
    log.warning(
        "LAW_MCP_MAX_PATTERN_LENGTH=%d jest poza dozwolonym zakresem %d-%d; serwer używa wartości efektywnej %d.",
        current.max_pattern_length,
        MAX_PATTERN_LENGTH_FLOOR,
        MAX_PATTERN_LENGTH_CEILING,
        current.effective_max_pattern_length,
    )


def log_remote_bind_warning(current: Settings, log: logging.Logger) -> None:
    """Warn when the HTTP listener reaches beyond the loopback.

    Reaching this point means the configuration already passed validation, so
    authentication is configured. The warning is not an error — it is the line
    an operator greps for when asking what a container actually exposes.
    """
    if current.transport != "streamable-http" or is_loopback_entry(current.host):
        return
    log.warning(
        "Serwer HTTP nasłuchuje na %s:%d — poza pętlą zwrotną. Tryb uwierzytelniania: %s.",
        current.host,
        current.port,
        current.auth_mode,
    )


settings = Settings()
