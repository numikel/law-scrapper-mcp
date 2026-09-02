"""Configuration settings for Law Scrapper MCP."""

from __future__ import annotations

import json
import logging
from ipaddress import ip_network
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AnyHttpUrl,
    Field,
    SecretStr,
    ValidationError,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from .config_primitives import MIN_AUTH_TOKEN_BYTES, _host_of, is_loopback_entry
from .config_validation import enforce_security_boundary

# Re-exported: `_host_of`, `is_loopback_entry` and `MIN_AUTH_TOKEN_BYTES` live in
# `config_primitives.py` (dependency-free, importable by `config_validation.py`
# without a cycle), but stay accessible as `law_scrapper_mcp.config.*` because
# `log_remote_bind_warning` below uses `is_loopback_entry`, and existing tests
# (`test_config_helpers.py`, `test_auth_settings_validation.py`) import all three
# straight from this module.
__all__ = [
    "MIN_AUTH_TOKEN_BYTES",
    "Settings",
    "USER_AGENT_CONTACT",
    "_host_of",
    "is_loopback_entry",
    "log_pattern_limit_clamping",
    "log_remote_bind_warning",
    "settings",
]

MAX_PATTERN_LENGTH_FLOOR = 64
MAX_PATTERN_LENGTH_CEILING = 4096
FILTER_MAX_RECORDS_FLOOR = 1

# Contact channel advertised to api.sejm.gov.pl. The API is run by a state
# institution, so its administrator needs a way to reach us that is not a ban.
USER_AGENT_CONTACT = "https://github.com/numikel/law-scrapper-mcp"

LOOPBACK_ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
LOOPBACK_ALLOWED_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]


# Human-readable bands for the egress knobs, quoted by the validator below. Kept next
# to the fields they describe would be nicer, but a `Field` cannot carry a message.
_EGRESS_BANDS = {
    "api_rate_per_second": "0.1-100",
    "api_rate_burst": "1-1000",
    "api_max_server_pause": "0-600 (powyżej zera)",
}


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    model_config = SettingsConfigDict(env_prefix="LAW_MCP_", hide_input_in_errors=True)

    # Transport
    transport: Literal["stdio", "streamable-http"] = "stdio"
    # Loopback by default (D7). This is a safe bind, not an access-control
    # mechanism — exposure is a deliberate act requiring an authentication mode.
    host: str = "127.0.0.1"
    port: int = 7683

    @field_validator("api_rate_per_second", "api_rate_burst", "api_max_server_pause", mode="wrap")
    @classmethod
    def _describe_egress_bounds(
        cls, value: object, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> object:
        """Name the variable and its band instead of pydantic's bare "less than" line.

        The constraints stay on the `Field` so the schema tells the truth; this only
        rewrites the failure so an operator reading a startup crash knows which
        `LAW_MCP_*` variable to fix without opening the source. The input is not
        echoed, matching `hide_input_in_errors`.
        """
        try:
            return handler(value)
        except ValidationError as error:
            name = info.field_name or ""
            band = _EGRESS_BANDS[name]
            raise ValueError(f"LAW_MCP_{name.upper()} musi być liczbą z zakresu {band}.") from error

    @field_validator("trusted_proxies")
    @classmethod
    def _validate_trusted_proxies(cls, value: list[str]) -> list[str]:
        """Reject malformed CIDR entries at startup instead of on the first request.

        `RateLimitMiddleware.__init__` also calls `ip_network()` on this list —
        without this validator, a bad entry there raises a raw `ValueError`
        instead of a clean, Polish-language `ValidationError`.
        """
        for entry in value:
            try:
                ip_network(entry, strict=False)
            except ValueError as error:
                raise ValueError(f"LAW_MCP_TRUSTED_PROXIES zawiera nieprawidłowy wpis '{entry}': {error}") from error
        return value

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
        """Delegates to `config_validation.enforce_security_boundary`."""
        return enforce_security_boundary(self)

    # Graceful shutdown window handed to uvicorn as `timeout_graceful_shutdown`.
    # Shorter than `api_timeout` on purpose: the trade-off between restart speed
    # and the share of requests allowed to finish is settled in favour of a fast
    # restart. Deployments that want it the other way round raise the value.
    # An int, because uvicorn's knob is whole seconds: a float here with an
    # `int()` cast downstream silently truncated `2.5` to `2` (#31). Pydantic
    # rejects a fractional value outright and `ge=1` keeps zero out. The
    # deployment contract is `stop_grace_period >= 2 * shutdown_grace` — see
    # docker-compose.yml.
    shutdown_grace: int = Field(default=15, ge=1)

    # Network boundary (F18). The defaults reproduce, value for value, the
    # constant that used to be hardcoded in server.py:43-47 — introducing the
    # fields changes nothing but the ability to override them.
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=lambda: list(LOOPBACK_ALLOWED_HOSTS))
    allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: list(LOOPBACK_ALLOWED_ORIGINS))

    # Authentication (F17)
    auth_mode: Literal["none", "bearer", "oauth"] = "none"
    auth_token: SecretStr | None = None
    auth_token_file: Path | None = None
    auth_issuer: AnyHttpUrl | None = None
    auth_audience: str | None = None
    auth_jwks_uri: AnyHttpUrl | None = None
    auth_resource_server_url: AnyHttpUrl | None = None
    auth_required_scopes: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # An allowlist handed to the decoder, never read from the token header:
    # a decoder honouring `alg` accepts `none`, or HS256 verified with the
    # public JWKS key the attacker already has.
    auth_algorithms: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["RS256", "ES256"])
    auth_jwks_cache_ttl: int = 3600

    # Inbound rate limiting (F26). Outbound throttling towards api.sejm.gov.pl
    # is a different budget in a different layer — see cluster 8.
    rate_limit_enabled: bool = True
    rate_limit_requests: int = Field(default=60, ge=1)
    rate_limit_window: float = Field(default=60.0, gt=0)
    rate_limit_burst: int = Field(default=10, ge=1)
    trusted_proxies: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator(
        "allowed_hosts",
        "allowed_origins",
        "auth_required_scopes",
        "auth_algorithms",
        "trusted_proxies",
        mode="before",
    )
    @classmethod
    def _accept_comma_separated_list(cls, value: object) -> object:
        """Read a list setting from the environment as comma-separated text.

        Without this, pydantic-settings decodes every complex field from the
        environment as JSON, and does it inside the settings source — before any
        validator on this model runs. `LAW_MCP_ALLOWED_HOSTS=mcp.example.com:*`
        therefore died with a bare `SettingsError` naming `EnvSettingsSource`,
        never reaching the Polish diagnostics below, and the reverse-proxy recipe
        in README.md could not work as written. `NoDecode` on the fields hands us
        the raw string instead, so both spellings are accepted here: JSON stays
        valid for anyone already using it, comma-separated is what the docs show.
        """
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                # Not JSON after all. A bracketed IPv6 literal opens and closes
                # with the same characters as a JSON array — `[::1]:*` is this
                # project's own default allowlist entry, and a lone `[::1]` in
                # LAW_MCP_TRUSTED_PROXIES is indistinguishable by shape. Falling
                # through to the comma reading keeps those working.
                pass
        return [item.strip() for item in text.split(",") if item.strip()]

    # API client
    api_timeout: float = 30.0
    # Two classes of outbound traffic (D4): this bounds the light JSON calls,
    # `api_max_concurrent_content` the heavy HTML/PDF downloads. The sum is the peak
    # the Sejm API sees, and the split keeps it at the pre-split ten.
    api_max_concurrent: int = Field(default=8, ge=1)
    api_max_concurrent_content: int = Field(default=2, ge=1)
    # Egress pace (D1). Concurrency alone bounds nothing: a sequential loop reaches any
    # rate at all, because a released slot is taken again immediately. A zero rate is
    # rejected rather than clamped — it would wedge every request on a bucket that
    # never refills. Bounded above as well: `inf` used to pass `gt=0` and turned the
    # bucket into no limiter at all, `nan` poisoned every refill comparison, and a
    # burst of a million tokens is a limiter that never engages.
    api_rate_per_second: float = Field(default=5.0, ge=0.1, le=100, allow_inf_nan=False)
    api_rate_burst: int = Field(default=10, ge=1, le=1000)
    # Longest client-wide pause one `Retry-After` header may impose. The request that
    # received the header has its own give-up check; this bounds what it costs every
    # other caller. Ten minutes is already past what any MCP client waits.
    api_max_server_pause: float = Field(default=60.0, gt=0, le=600, allow_inf_nan=False)
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

    # Logging. A closed set rather than `str`: `setup_logging` used to fall back
    # to INFO for anything `logging` did not know, so `WARN` or `FATAL` quietly
    # produced INFO output on both transports (#31).
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["text", "json"] = "text"

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        """Accept `info` as well as `INFO`; anything else is left to the Literal."""
        if isinstance(value, str):
            upper = value.strip().upper()
            if upper not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
                raise ValueError(
                    f"LAW_MCP_LOG_LEVEL={value!r} nie jest obsługiwanym poziomem. "
                    "Dozwolone wartości: DEBUG, INFO, WARNING, ERROR, CRITICAL."
                )
            return upper
        return value

    # Server info
    server_name: str = "law-scrapper-mcp"
    server_version: str = "4.1.0"

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
