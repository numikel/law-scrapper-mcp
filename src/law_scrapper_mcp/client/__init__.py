"""HTTP client for Sejm API."""

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.egress import (
    CallerIdentity,
    EgressClient,
    EgressDefaults,
    EgressHost,
    EgressHub,
    SourceState,
)
from law_scrapper_mcp.client.exceptions import (
    ActNotFoundError,
    ApiUnavailableError,
    ContentNotAvailableError,
    ContentTooLargeError,
    DocumentNotLoadedError,
    EgressConfigError,
    EgressRedirectRefusedError,
    InvalidEliError,
    LawScrapperError,
    ResponseTooLargeError,
    SejmApiError,
)
from law_scrapper_mcp.client.sejm_client import SejmApiClient

__all__ = [
    "ActNotFoundError",
    "ApiUnavailableError",
    "CallerIdentity",
    "ContentNotAvailableError",
    "ContentTooLargeError",
    "DocumentNotLoadedError",
    "EgressClient",
    "EgressConfigError",
    "EgressDefaults",
    "EgressHost",
    "EgressHub",
    "EgressRedirectRefusedError",
    "InvalidEliError",
    "LawScrapperError",
    "ResponseTooLargeError",
    "SejmApiError",
    "SejmApiClient",
    "SourceState",
    "TTLCache",
]
