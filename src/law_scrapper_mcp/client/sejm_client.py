"""Async HTTP client for Sejm API."""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic
from typing import Any

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.circuit_breaker import CircuitBreaker
from law_scrapper_mcp.client.egress import (
    DEFAULT_USER_AGENT,
    SEJM_MESSAGES,
    EgressClient,
    EgressHost,
    RequestClass,
)
from law_scrapper_mcp.client.exceptions import SEJM_SOURCE
from law_scrapper_mcp.client.rate_limiter import RateLimiter

__all__ = ["DEFAULT_USER_AGENT", "RequestClass", "SejmApiClient"]


class SejmApiClient(EgressClient):
    """Async HTTP client for Sejm API with retry, caching and circuit breaker.

    The transport, the retry loop and every politeness control live in `EgressClient`; this
    class only knows the Sejm's address and endpoints. Its host is deliberately configured to
    keep the redirect behaviour it has always had (httpx's own cap of 20, no origin pin).
    """

    BASE_URL = "https://api.sejm.gov.pl/eli"

    def __init__(
        self,
        cache: TTLCache,
        timeout: float = 30.0,
        max_concurrent: int = 8,
        circuit_breaker: CircuitBreaker | None = None,
        max_attempts: int = 3,
        retry_budget: float = 45.0,
        user_agent: str = DEFAULT_USER_AGENT,
        max_concurrent_content: int = 2,
        rate_per_second: float = 5.0,
        rate_burst: int = 10,
        max_server_pause: float = 60.0,
        rate_limiter: RateLimiter | None = None,
        clock: Callable[[], float] = monotonic,
    ):
        super().__init__(
            host=EgressHost(
                name=SEJM_SOURCE,
                base_url=self.BASE_URL,
                messages=SEJM_MESSAGES,
                pin_origin=False,
                max_redirects=20,
            ),
            cache=cache,
            timeout=timeout,
            max_concurrent=max_concurrent,
            circuit_breaker=circuit_breaker,
            max_attempts=max_attempts,
            retry_budget=retry_budget,
            user_agent=user_agent,
            max_concurrent_content=max_concurrent_content,
            rate_per_second=rate_per_second,
            rate_burst=rate_burst,
            max_server_pause=max_server_pause,
            rate_limiter=rate_limiter,
            clock=clock,
        )

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

    async def get_act_structure(self, publisher: str, year: int, pos: int) -> list[dict[str, Any]]:
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

    async def get_act_references(self, publisher: str, year: int, pos: int) -> dict[str, Any]:
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

    async def get_act_html(self, publisher: str, year: int, pos: int, *, max_bytes: int | None = None) -> str:
        """Get act HTML content.

        Args:
            publisher: Publisher code
            year: Year
            pos: Position number
            max_bytes: Byte budget for the body; `None` reads it whole.

        Returns:
            HTML content
        """
        path = f"acts/{publisher}/{year}/{pos}/text.html"
        return await self.get_text(path, max_bytes=max_bytes)

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
