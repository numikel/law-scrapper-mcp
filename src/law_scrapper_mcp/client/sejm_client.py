"""Async HTTP client for Sejm API."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.circuit_breaker import CircuitBreaker
from law_scrapper_mcp.client.exceptions import (
    ActNotFoundError,
    ApiUnavailableError,
    SejmApiError,
)


class SejmApiClient:
    """Async HTTP client for Sejm API with retry, caching and circuit breaker."""

    BASE_URL = "https://api.sejm.gov.pl/eli"

    def __init__(
        self,
        cache: TTLCache,
        timeout: float = 30.0,
        max_concurrent: int = 10,
        circuit_breaker: CircuitBreaker | None = None,
    ):
        self._client: httpx.AsyncClient | None = None
        self._cache = cache
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._timeout = timeout
        self._circuit_breaker = circuit_breaker or CircuitBreaker()

    async def start(self) -> None:
        """Initialize the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=5.0, read=self._timeout, write=10.0, pool=10.0
                ),
                headers={
                    "User-Agent": "law-scrapper-mcp/2.0",
                    "Accept": "application/json",
                },
                follow_redirects=True,
            )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
    )
    async def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        """Make HTTP request with retry logic and circuit breaker.

        Args:
            method: HTTP method
            path: URL path (relative to BASE_URL)
            **kwargs: Additional httpx request parameters

        Returns:
            HTTP response

        Raises:
            ActNotFoundError: If resource not found (404)
            ApiUnavailableError: If API is unavailable (502, 503) or circuit breaker open
            SejmApiError: For other HTTP errors
        """
        if not self._circuit_breaker.can_execute():
            raise ApiUnavailableError(
                "API Sejmu tymczasowo niedostępne (circuit breaker otwarty)",
                status_code=503,
            )

        if self._client is None:
            await self.start()

        assert self._client is not None  # ensured by start()

        url = f"{self.BASE_URL}/{path.lstrip('/')}"

        async with self._semaphore:
            try:
                response = await self._client.request(method, url, **kwargs)
                response.raise_for_status()
                self._circuit_breaker.record_success()
                return response
            except httpx.TimeoutException:
                self._circuit_breaker.record_failure()
                raise
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise ActNotFoundError(path) from e
                elif e.response.status_code in (502, 503):
                    self._circuit_breaker.record_failure()
                    raise ApiUnavailableError(
                        f"API temporarily unavailable: {e.response.status_code}",
                        status_code=e.response.status_code,
                        url=url,
                    ) from e
                else:
                    raise SejmApiError(
                        f"HTTP {e.response.status_code}: {e.response.text}",
                        status_code=e.response.status_code,
                        url=url,
                    ) from e

    async def get_json(
        self, path: str, params: dict[str, Any] | None = None, cache_ttl: int | None = None
    ) -> Any:
        """Get JSON response from API with optional caching.

        Args:
            path: URL path
            params: Query parameters
            cache_ttl: Cache TTL in seconds (None = no cache)

        Returns:
            Parsed JSON response
        """
        # Build cache key
        cache_key = None
        if cache_ttl is not None:
            cache_key = f"json:{path}:{params or {}}"
            cached = await self._cache.get(cache_key)
            if cached is not None:
                return cached

        response = await self._request("GET", path, params=params)
        data = response.json()

        if cache_key is not None and cache_ttl is not None:
            await self._cache.set(cache_key, data, cache_ttl)

        return data

    async def get_text(self, path: str) -> str:
        """Get text response from API.

        Args:
            path: URL path

        Returns:
            Response text
        """
        response = await self._request(
            "GET", path, headers={"Accept": "text/html, text/plain, */*"}
        )
        return response.text

    async def get_bytes(self, path: str) -> bytes:
        """Get binary response from API.

        Args:
            path: URL path

        Returns:
            Response bytes
        """
        response = await self._request(
            "GET", path, headers={"Accept": "application/pdf, application/octet-stream, */*"}
        )
        return response.content

    async def get_act(self, publisher: str, year: int, pos: int) -> dict[str, Any]:
        """Get act details.

        Args:
            publisher: Publisher code
            year: Year
            pos: Position number

        Returns:
            Act details as dict
        """
        path = f"acts/{publisher}/{year}/{pos}"
        return await self.get_json(path)

    async def search_acts(self, params: dict[str, Any]) -> dict[str, Any]:
        """Search for acts.

        Args:
            params: Search parameters

        Returns:
            Search results as dict
        """
        return await self.get_json("acts/search", params=params)

    async def get_act_structure(
        self, publisher: str, year: int, pos: int
    ) -> list[dict[str, Any]]:
        """Get act table of contents structure.

        Args:
            publisher: Publisher code
            year: Year
            pos: Position number

        Returns:
            Structure as list of dicts
        """
        path = f"acts/{publisher}/{year}/{pos}/struct"
        return await self.get_json(path)

    async def get_act_references(
        self, publisher: str, year: int, pos: int
    ) -> dict[str, Any]:
        """Get act references/relationships.

        Args:
            publisher: Publisher code
            year: Year
            pos: Position number

        Returns:
            References as dict
        """
        path = f"acts/{publisher}/{year}/{pos}/references"
        return await self.get_json(path)

    async def get_act_html(self, publisher: str, year: int, pos: int) -> str:
        """Get act HTML content.

        Args:
            publisher: Publisher code
            year: Year
            pos: Position number

        Returns:
            HTML content
        """
        path = f"acts/{publisher}/{year}/{pos}/text.html"
        return await self.get_text(path)

    async def get_act_pdf_url(self, publisher: str, year: int, pos: int) -> str:
        """Get act PDF URL.

        Args:
            publisher: Publisher code
            year: Year
            pos: Position number

        Returns:
            PDF URL
        """
        return f"{self.BASE_URL}/acts/{publisher}/{year}/{pos}/text.pdf"

    async def get_metadata(self, endpoint: str) -> list[dict[str, Any]] | dict[str, Any]:
        """Get metadata from endpoint.

        Args:
            endpoint: Metadata endpoint (e.g., "keywords", "publishers")

        Returns:
            Metadata as list or dict
        """
        return await self.get_json(endpoint)
