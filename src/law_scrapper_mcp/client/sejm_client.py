"""Async HTTP client for Sejm API."""

from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any

import httpx

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.circuit_breaker import CircuitBreaker
from law_scrapper_mcp.client.exceptions import (
    ActNotFoundError,
    ApiUnavailableError,
    SejmApiError,
)
from law_scrapper_mcp.client.failure_policy import backoff, classify_failure


async def _delay(seconds: float) -> None:
    """Jedyny punkt oczekiwania w pętli ponowień.

    Wydzielone, żeby testy mogły podmienić opóźnienie zamiast je odczekiwać.
    """
    await asyncio.sleep(seconds)


class SejmApiClient:
    """Async HTTP client for Sejm API with retry, caching and circuit breaker."""

    BASE_URL = "https://api.sejm.gov.pl/eli"

    def __init__(
        self,
        cache: TTLCache,
        timeout: float = 30.0,
        max_concurrent: int = 10,
        circuit_breaker: CircuitBreaker | None = None,
        max_attempts: int = 3,
        retry_budget: float = 45.0,
    ):
        self._client: httpx.AsyncClient | None = None
        self._cache = cache
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._timeout = timeout
        self._circuit_breaker = circuit_breaker or CircuitBreaker()
        self._max_attempts = max_attempts
        self._retry_budget = retry_budget

    async def start(self) -> None:
        """Initialize the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=self._timeout, write=10.0, pool=10.0),
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

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Wyślij pojedyncze żądanie HTTP.

        Warstwa najniższa: buduje URL, pilnuje semafora i podnosi wyłącznie
        wyjątki `httpx`. Nie ponawia i nie tłumaczy błędów.

        Raises:
            httpx.HTTPError: Dowolny błąd transportowy lub statusowy.
        """
        if self._client is None:
            await self.start()

        assert self._client is not None  # ensured by start()

        url = f"{self.BASE_URL}/{path.lstrip('/')}"

        async with self._semaphore:
            response = await self._client.request(method, url, **kwargs)
            response.raise_for_status()
            return response

    async def _execute_with_resilience(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Wykonaj żądanie z ponawianiem, budżetem czasu i księgowaniem wyłącznika.

        Warstwa środkowa widzi wyłącznie surowe wyjątki `httpx`, więc żaden blok
        `except` nie może zmienić typu, zanim polityka zdąży go ocenić.

        Jedna operacja użytkownika rejestruje najwyżej jedną awarię wyłącznika,
        a stan wyłącznika sprawdzany jest przed każdą próbą — obwód otwarty przez
        równoległy ruch przerywa sekwencję zamiast dobijać API.

        Raises:
            ApiUnavailableError: Gdy wyłącznik nie wpuszcza żądania.
            httpx.HTTPError: Ostatni błąd po wyczerpaniu prób lub budżetu.
        """
        deadline = monotonic() + self._retry_budget
        throttled = False

        for attempt in range(1, self._max_attempts + 1):
            if not self._circuit_breaker.try_acquire():
                raise ApiUnavailableError(
                    "API Sejmu tymczasowo niedostępne (bezpiecznik otwarty)",
                    status_code=503,
                )

            try:
                response = await self._send(method, path, **kwargs)
            except httpx.HTTPError as exc:
                verdict = classify_failure(exc)
                delay = verdict.retry_after if verdict.retry_after is not None else backoff(attempt)

                give_up = (
                    not verdict.retryable
                    or attempt == self._max_attempts
                    or (verdict.rate_limited and throttled)
                    or monotonic() + delay >= deadline
                )

                if give_up:
                    if verdict.breaker_failure:
                        self._circuit_breaker.release_failure()
                    else:
                        self._circuit_breaker.release_probe()
                    raise

                self._circuit_breaker.release_probe()
                if verdict.rate_limited:
                    throttled = True
                await _delay(delay)
            else:
                self._circuit_breaker.release_success()
                return response

        # Nieosiągalne: pętla zawsze kończy się przez return albo raise.
        raise ApiUnavailableError("API Sejmu nie odpowiedziało w ramach dozwolonych prób", status_code=503)

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Wykonaj żądanie i przetłumacz błędy `httpx` na wyjątki domenowe.

        Warstwa najwyższa. Translacja dzieje się na zewnątrz ponawiania, więc
        polityka zawsze ocenia oryginalny typ wyjątku.

        Args:
            method: Metoda HTTP.
            path: Ścieżka względem BASE_URL.
            **kwargs: Dodatkowe parametry żądania httpx.

        Returns:
            Odpowiedź HTTP.

        Raises:
            ActNotFoundError: Gdy zasób nie istnieje (404).
            ApiUnavailableError: Gdy API zwróciło 5xx, zawiódł transport
                albo wyłącznik jest otwarty.
            SejmApiError: Dla pozostałych błędów po stronie zapytania.
        """
        try:
            return await self._execute_with_resilience(method, path, **kwargs)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            url = str(exc.request.url)
            if status == 404:
                raise ActNotFoundError(path) from exc
            if 500 <= status <= 599:
                raise ApiUnavailableError(
                    f"API Sejmu chwilowo niedostępne (HTTP {status})",
                    status_code=status,
                    url=url,
                ) from exc
            raise SejmApiError(
                f"HTTP {status}: {exc.response.text}",
                status_code=status,
                url=url,
            ) from exc
        except httpx.TransportError as exc:
            raise ApiUnavailableError(f"Błąd połączenia z API Sejmu: {exc}") from exc
        except httpx.HTTPError as exc:
            raise SejmApiError(f"Błędne żądanie do API Sejmu: {exc}") from exc

    async def get_json(self, path: str, params: dict[str, Any] | None = None, cache_ttl: int | None = None) -> Any:
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
        response = await self._request("GET", path, headers={"Accept": "text/html, text/plain, */*"})
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
