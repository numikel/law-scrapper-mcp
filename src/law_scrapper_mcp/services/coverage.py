"""What each source covers, in one source-neutral language.

The core and every source module describe their coverage with the same types: a `SourceCoverage`
made of `CoverageSegment`s (one per publisher, court type and so on). A source registers a
`CoverageProvider` in the `CoverageRegistry`; the registry measures it lazily, on the first question,
and remembers the answer for a day, so a question costs the upstream host nothing most of the time.

`act_coverage_gap` is the one rule that decides whether an act query reaches past what a segment
covers, and returns the Polish sentence the agent reads.

The registry lives in `services/` for the reason `act_sources.py` does: the services consult it and
must not import `source_modules`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal, Protocol

from law_scrapper_mcp.client.exceptions import LawScrapperError

logger = logging.getLogger(__name__)

# How long a successful measurement is trusted.
COVERAGE_TTL_SECONDS = 86_400
# How long the fallback (or the absence of one) stands in for a failed measurement, so a dead host is
# not asked again with every query.
FALLBACK_TTL_SECONDS = 900
# A query whose upper bound lies further than this past the newest announcement touches a gap.
STALE_AFTER_DAYS = 30

FALLBACK_NOTE = "Wartość zapasowa — bieżący pomiar się nie udał."

CoverageOrigin = Literal["measured", "fallback"]


class CoverageConflictError(LawScrapperError):
    """A provider tried to register under a key another provider already holds."""


@dataclass(frozen=True, slots=True)
class CoverageSegment:
    """One slice of a source: a publisher, a court type, a journal.

    Attributes:
        key: Stable identifier of the slice, e.g. `DU`, `POL_WOJ_PM`, `SUPREME`. For an act publisher
            it is the canonical publisher code.
        label: Polish name shown to the agent.
        newest: Date of the newest record the slice holds; `None` when unknown.
        newest_ref: How to find that record (`2026/5811`, a case signature); `None` when unknown.
        count: Number of records in the slice; `None` when unknown.
        years: Publication years the slice has records for; `None` when the source cannot tell.
    """

    key: str
    label: str
    newest: date | None = None
    newest_ref: str | None = None
    count: int | None = None
    years: tuple[int, ...] | None = None


@dataclass(frozen=True, slots=True)
class SourceCoverage:
    """The coverage of one source, as measured on one day.

    Attributes:
        source: Name of the source, e.g. `sejm`, `voivodeship-journals`, `saos`.
        segments: The slices the source is made of.
        measured_on: The day the numbers were taken.
        origin: `measured` for a live measurement, `fallback` for the provider's last known values.
    """

    source: str
    segments: tuple[CoverageSegment, ...]
    measured_on: date
    origin: CoverageOrigin = "measured"


class CoverageProvider(Protocol):
    """What a source gives the registry to describe its coverage."""

    key: str
    """Registry key; for a provider that covers one act publisher, that publisher's canonical code."""

    source: str
    """Name of the source the provider describes."""

    async def measure(self, today: date) -> SourceCoverage:
        """Take a live measurement; may raise, in which case the registry uses `fallback`."""
        ...

    def fallback(self) -> SourceCoverage:
        """Return the last known values (`origin="fallback"`), without touching the network."""
        ...


@dataclass(frozen=True, slots=True)
class _Entry:
    value: SourceCoverage | None
    expires_at: float


class CoverageRegistry:
    """Providers of coverage, measured lazily and remembered.

    One measurement per provider at a time: concurrent questions for the same key wait on a lock and
    share its result, so the host never sees a doubled series. A failed measurement is answered with
    the provider's fallback, which is remembered for a short while too. A provider whose fallback
    fails as well yields `None` (also remembered), never an exception.

    The clocks are injectable (`today` for the date handed to `measure`, `monotonic` for expiry) so
    tests need neither sleeping nor the wall clock.
    """

    def __init__(
        self,
        *,
        today: Callable[[], date] = date.today,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._today = today
        self._monotonic = monotonic
        self._providers: dict[str, CoverageProvider] = {}
        self._cache: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def today(self) -> date:
        """The date the registry works with (the injected clock)."""
        return self._today()

    def register(self, provider: CoverageProvider) -> None:
        """Add a provider under its `key`.

        Raises:
            CoverageConflictError: Another provider holds the key. Nothing is changed in that case.
        """
        if provider.key in self._providers:
            raise CoverageConflictError(f"Zasięg źródła {provider.key} ma już dostawcę ({provider.source}).")
        self._providers[provider.key] = provider
        self._cache.pop(provider.key, None)

    def unregister(self, key: str) -> None:
        """Drop the provider and whatever was remembered about it. An unknown key is ignored."""
        self._providers.pop(key, None)
        self._cache.pop(key, None)
        self._locks.pop(key, None)

    def keys(self) -> tuple[str, ...]:
        """Registered keys, sorted."""
        return tuple(sorted(self._providers))

    def _remembered(self, key: str) -> _Entry | None:
        entry = self._cache.get(key)
        if entry is not None and entry.expires_at > self._monotonic():
            return entry
        return None

    async def get(self, key: str) -> SourceCoverage | None:
        """Coverage for `key`; `None` for an unknown key or a provider that cannot answer at all."""
        provider = self._providers.get(key)
        if provider is None:
            return None
        entry = self._remembered(key)
        if entry is not None:
            return entry.value
        async with self._locks.setdefault(key, asyncio.Lock()):
            entry = self._remembered(key)
            if entry is not None:
                return entry.value
            return await self._refresh(key, provider)

    async def _refresh(self, key: str, provider: CoverageProvider) -> SourceCoverage | None:
        ttl = COVERAGE_TTL_SECONDS
        try:
            value: SourceCoverage | None = await provider.measure(self._today())
        except Exception as error:
            logger.warning(
                "Coverage measurement of %s failed (%s: %s); using the fallback", key, type(error).__name__, error
            )
            ttl = FALLBACK_TTL_SECONDS
            try:
                value = provider.fallback()
            except Exception as fallback_error:
                logger.warning(
                    "Coverage fallback of %s failed as well (%s: %s); skipping it",
                    key,
                    type(fallback_error).__name__,
                    fallback_error,
                )
                value = None
        # A provider unregistered (or replaced) while it was being measured must not leave its value behind.
        if self._providers.get(key) is provider:
            self._cache[key] = _Entry(value=value, expires_at=self._monotonic() + ttl)
        return value

    async def all(self) -> list[SourceCoverage]:
        """Coverage of every provider that can answer, measured concurrently, in key order."""
        answers = await asyncio.gather(*(self.get(key) for key in self.keys()))
        return [coverage for coverage in answers if coverage is not None]


def format_years(years: Iterable[int]) -> str:
    """Render years compactly as ranges, e.g. `2001–2003, 2005, 2009–2026` (en dash)."""
    ordered = sorted(set(years))
    if not ordered:
        return "brak"
    parts: list[str] = []
    start = previous = ordered[0]
    for year in [*ordered[1:], None]:
        if year is not None and year == previous + 1:
            previous = year
            continue
        parts.append(str(start) if start == previous else f"{start}–{previous}")
        if year is not None:
            start = previous = year
    return ", ".join(parts)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _upper_bound(year: int | None, pub_date_to: str | None, today: date) -> date | None:
    """Latest announcement date the query asks for; `None` when it cannot be told."""
    if pub_date_to:
        return _parse_date(pub_date_to)
    if year is not None:
        try:
            return min(date(year, 12, 31), today)
        except ValueError:
            return None
    return today


def act_coverage_gap(
    segment: CoverageSegment,
    *,
    year: int | None,
    pub_date_from: str | None,
    pub_date_to: str | None,
    today: date,
    origin: CoverageOrigin,
    measured_on: date | None = None,
) -> str | None:
    """Decide whether an act query reaches past what `segment` covers; return the Polish sentence or `None`.

    Two gaps, checked in this order and reported singly (a missing year makes the freshness question moot):

    * year: `year` is given, the segment knows its years and `year` is not among them. A fallback
      without `years` therefore never reports this gap.
    * freshness: the upper bound of the window lies more than `STALE_AFTER_DAYS` past
      `segment.newest`. The bound is `pub_date_to`, else the end of `year` capped at `today`, else
      `today`. Exactly 30 days past is not a gap, 31 is.

    A fallback origin appends a note that the current measurement failed.

    Args:
        segment: The slice the query targets.
        year: Publication year asked for.
        pub_date_from: Lower bound of the announcement window. The upper bound decides freshness, so
            this is accepted for symmetry with the search parameters and not read.
        pub_date_to: Upper bound of the announcement window (`YYYY-MM-DD`); unparseable means unknown.
        today: The current date.
        origin: Where the segment's numbers came from.
        measured_on: Day of the measurement, quoted in the sentence when given.
    """
    measured = f"; pomiar z {measured_on}" if measured_on is not None else ""
    measured_tail = f" (pomiar z {measured_on})" if measured_on is not None else ""
    message: str | None = None
    if year is not None and segment.years is not None and year not in segment.years:
        # The label is quoted, not declined: names such as "Dz. Urz. Woj. Pomorskiego" cannot be inflected.
        message = (
            f"Źródło „{segment.label}” nie ma rocznika {year} (dostępne: {format_years(segment.years)}{measured})."
        )
    elif segment.newest is not None:
        upper = _upper_bound(year, pub_date_to, today)
        if upper is not None and upper > segment.newest + timedelta(days=STALE_AFTER_DAYS):
            ref = f" ({segment.newest_ref})" if segment.newest_ref else ""
            message = (
                f"Najnowsze ogłoszenie w źródle „{segment.label}” pochodzi z {segment.newest}{ref}; "
                f"zapytanie sięga dalej{measured_tail}."
            )
    if message is not None and origin == "fallback":
        message = f"{message} {FALLBACK_NOTE}"
    return message
