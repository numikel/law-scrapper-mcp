"""HTTP client for Sejm API."""

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.exceptions import (
    ActNotFoundError,
    ApiUnavailableError,
    ContentNotAvailableError,
    DocumentNotLoadedError,
    InvalidEliError,
    LawScrapperError,
    SejmApiError,
)
from law_scrapper_mcp.client.sejm_client import SejmApiClient

__all__ = [
    "ActNotFoundError",
    "ApiUnavailableError",
    "ContentNotAvailableError",
    "DocumentNotLoadedError",
    "InvalidEliError",
    "LawScrapperError",
    "SejmApiError",
    "SejmApiClient",
    "TTLCache",
]
