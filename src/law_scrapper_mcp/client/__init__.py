"""HTTP client for Sejm API."""

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.egress import EgressClient
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
    "ContentNotAvailableError",
    "ContentTooLargeError",
    "DocumentNotLoadedError",
    "EgressClient",
    "EgressConfigError",
    "EgressRedirectRefusedError",
    "InvalidEliError",
    "LawScrapperError",
    "ResponseTooLargeError",
    "SejmApiError",
    "SejmApiClient",
    "TTLCache",
]
