"""Coverage of the two publishers the Sejm API serves itself: Dziennik Ustaw (`DU`) and Monitor Polski (`MP`).

Both providers measure through the one `SejmApiClient` the server already holds, so every request goes
through its rate limiter, circuit breaker and cache; nothing here builds an HTTP client of its own.

A measurement is two reads per publisher, one of them shared:

1. `GET acts`, the publisher list, which carries every publisher's years and act count. The list is
   read once for both providers (the same call and cache key as the `publishers` metadata category,
   so it usually costs no request at all).
2. `GET acts/search?publisher=X&year=<newest year>&limit=1`. On both publishers the first item of a
   year page is the act with the highest position (`tests/fixtures/sejm/PROVENANCE.md`), and its
   `promulgation` is the publication date.

Three requests at most per process per day. When a measurement fails the registry uses `fallback`:
the values recorded on 2026-11-08, kept here as constants because the sdist of the core carries
`src/` only and the container image carries no tests.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Any

from law_scrapper_mcp.client.exceptions import SEJM_SOURCE, SejmApiError
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.config import settings
from law_scrapper_mcp.services.coverage import CoverageProvider, CoverageRegistry, CoverageSegment, SourceCoverage

__all__ = ["SejmCoverageProvider", "register_sejm_coverage"]

# The day the fallback values were recorded (`tests/fixtures/sejm/PROVENANCE.md`, captured 2026-11-08).
FALLBACK_MEASURED_ON = date(2026, 11, 8)

_SENTINEL_YEAR = "0001-"


@dataclass(frozen=True, slots=True)
class _Publisher:
    """What is known about one publisher without a network call."""

    code: str
    label: str
    newest: date
    newest_ref: str
    count: int


# Newest announcement and act count per publisher on 2026-11-08. `years` is left out of the fallback on
# purpose: a stale list of years would report a year gap for a year published since.
_PUBLISHERS: tuple[_Publisher, ...] = (
    _Publisher("DU", "Dziennik Ustaw", date(2026, 11, 5), "2026/1465", 98_051),
    _Publisher("MP", "Monitor Polski", date(2026, 11, 5), "2026/1100", 66_835),
)


def _to_date(value: object) -> date | None:
    """Calendar day of a wire date or timestamp; `None` for a sentinel, an empty or an unreadable value."""
    if not isinstance(value, str) or value.startswith(_SENTINEL_YEAR):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


class _PublisherList:
    """The Sejm's publisher list, read once for both providers.

    Providers are measured concurrently, so two cold reads would race past the client's cache. The
    lock makes the second reader wait and find the first one's answer in that cache.
    """

    def __init__(self, client: SejmApiClient) -> None:
        self._client = client
        self._lock = asyncio.Lock()

    async def read(self) -> list[dict[str, Any]]:
        async with self._lock:
            data = await self._client.get_json("acts", cache_ttl=settings.cache_metadata_ttl)
        if not isinstance(data, list):
            raise SejmApiError("Sejm zwrócił listę wydawców w nieoczekiwanym kształcie.", source=SEJM_SOURCE)
        return [entry for entry in data if isinstance(entry, dict)]


class SejmCoverageProvider:
    """Coverage of one Sejm publisher (`DU` or `MP`); the registry key is the publisher code."""

    source = "sejm"

    def __init__(self, publisher: _Publisher, client: SejmApiClient, publishers: _PublisherList) -> None:
        self.key = publisher.code
        self._publisher = publisher
        self._client = client
        self._publishers = publishers

    async def measure(self, today: date) -> SourceCoverage:
        """Read the years and the act count, then the newest act of the newest year.

        Raises:
            SejmApiError: The Sejm answered in a shape without this publisher or its years.
        """
        code = self._publisher.code
        entry = next((item for item in await self._publishers.read() if item.get("code") == code), None)
        raw_years = entry.get("years") if entry is not None else None
        if not isinstance(raw_years, list) or not raw_years:
            raise SejmApiError(f"Sejm nie podał roczników wydawcy {code}.", source=SEJM_SOURCE)
        years = tuple(sorted({int(year) for year in raw_years}))
        newest_year = years[-1]
        # The parameter order matches `SearchService.search`, so a `limit=1` search shares the cache key.
        page = await self._client.get_json(
            "acts/search",
            params={"publisher": code, "year": newest_year, "limit": 1},
            cache_ttl=settings.cache_search_ttl,
        )
        items = page.get("items") if isinstance(page, dict) else None
        first = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
        pos = first.get("pos")
        count = entry.get("actsCount") if entry is not None else None
        segment = CoverageSegment(
            key=code,
            label=self._publisher.label,
            newest=_to_date(first.get("promulgation")),
            newest_ref=f"{newest_year}/{pos}" if isinstance(pos, int) else None,
            count=count if isinstance(count, int) else None,
            years=years,
        )
        return SourceCoverage(source=self.source, segments=(segment,), measured_on=today, origin="measured")

    def fallback(self) -> SourceCoverage:
        """The values recorded on 2026-11-08, without years and without touching the network."""
        publisher = self._publisher
        segment = CoverageSegment(
            key=publisher.code,
            label=publisher.label,
            newest=publisher.newest,
            newest_ref=publisher.newest_ref,
            count=publisher.count,
            years=None,
        )
        return SourceCoverage(
            source=self.source, segments=(segment,), measured_on=FALLBACK_MEASURED_ON, origin="fallback"
        )


def register_sejm_coverage(registry: CoverageRegistry, client: SejmApiClient) -> tuple[CoverageProvider, ...]:
    """Register the `DU` and `MP` providers on `registry`, both reading through `client`."""
    publishers = _PublisherList(client)
    providers = tuple(SejmCoverageProvider(publisher, client, publishers) for publisher in _PUBLISHERS)
    for provider in providers:
        registry.register(provider)
    return providers
