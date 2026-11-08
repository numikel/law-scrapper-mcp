"""Coverage of the voivodeship journals: one provider per served journal, over that journal's own client.

The order in which a journal lists its acts cannot be trusted: two hosts (SK and WP) do not put the
newest act first, `sortBy`/`sortDir` are ignored, and `pubDateFrom` without a year and `pubDateTo` is
ignored too (`tests/fixtures/edziennik/PROVENANCE.md`, fourth session). Positions, however, are assigned
in publication order, so the newest act of the newest year sits at position `totalCount`. A measurement
therefore takes three requests of the one host:

1. `GET acts`: the years the journal has and its act count (camelCase and PascalCase hosts alike).
2. `GET search?year=<newest year>&limit=1`: only `totalCount` is read, never the first item.
3. `GET acts/{code}/{year}/{totalCount}`: the act detail, whose `promulgation` is the publication date.

When the newest year is still empty (`totalCount` 0, e.g. on 1 January), one search and one detail
request for the year before it replace steps 2 and 3. A 204 on the detail (the host's answer for a
missing act) leaves the newest date unknown, and nothing is retried.

`fallback` is the newest act recorded on 2026-11-05/06, as constants: the sdist of the core carries
`src/` only and the container image carries no tests, so a runtime read of a fixture would fail
exactly where the fallback is needed.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from law_scrapper_mcp.client.egress import EgressClient
from law_scrapper_mcp.client.exceptions import SejmApiError
from law_scrapper_mcp.services.coverage import CoverageSegment, SourceCoverage
from law_scrapper_mcp_voivodeship_journals import normalise
from law_scrapper_mcp_voivodeship_journals.catalog import Journal

__all__ = ["FALLBACK_MEASURED_ON", "NEWEST_RECORDED", "JournalCoverageProvider"]

SOURCE_NAME = "voivodeship-journals"

# The day the fallback values were put together from the recordings of 2026-11-05/06 and 2026-11-08.
FALLBACK_MEASURED_ON = date(2026, 11, 8)

# Newest act of each served journal as recorded: (publication date, `{year}/{pos}`). Ten hosts list
# their newest act first, so these come from the first item of the 2026 page (`announcementDate` of
# a search item is the publication date). SK and WP do not, so theirs come from the act detail at
# position `totalCount` (`promulgation` of a detail is the publication date).
NEWEST_RECORDED: dict[str, tuple[date, str]] = {
    "POL_WOJ_DS": (date(2026, 11, 6), "2026/4820"),
    "POL_WOJ_KP": (date(2026, 11, 6), "2026/5381"),
    "POL_WOJ_LB": (date(2026, 11, 6), "2026/5343"),
    "POL_WOJ_OP": (date(2026, 11, 6), "2026/2854"),
    "POL_WOJ_PK": (date(2026, 11, 6), "2026/4337"),
    "POL_WOJ_PL": (date(2026, 11, 6), "2026/4436"),
    "POL_WOJ_PM": (date(2026, 11, 5), "2026/4305"),
    "POL_WOJ_SL": (date(2026, 11, 6), "2026/6677"),
    "POL_WOJ_SK": (date(2026, 11, 6), "2026/3577"),
    "POL_WOJ_WM": (date(2026, 11, 6), "2026/4259"),
    "POL_WOJ_WP": (date(2026, 11, 6), "2026/8206"),
    "POL_WOJ_ZP": (date(2026, 11, 6), "2026/4852"),
}


def _int(value: object) -> int | None:
    """`value` when it is a real integer (a `bool` is not one), else `None`."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class JournalCoverageProvider:
    """Coverage of one journal; the registry key is the journal's publisher code."""

    source = SOURCE_NAME

    def __init__(self, journal: Journal, client: EgressClient) -> None:
        self.key = journal.code
        self._journal = journal
        self._client = client

    async def _total_for(self, year: int) -> int:
        """`totalCount` of the year's search, the position of the year's newest act."""
        page = normalise.camelise(await self._client.get_json("search", params={"year": year, "limit": 1}))
        total = _int(page.get("totalCount")) if isinstance(page, dict) else None
        if total is None:
            raise SejmApiError(f"Dziennik {self.key} zwrócił wynik wyszukiwania bez liczby aktów.", source=self.key)
        return total

    async def _newest(self, years: tuple[int, ...]) -> tuple[date | None, str | None]:
        """Date and `{year}/{pos}` of the newest act, or `(None, None)` when the host cannot show it."""
        year = years[-1]
        total = await self._total_for(year)
        if total == 0:
            if len(years) < 2:
                return None, None
            year = years[-2]
            total = await self._total_for(year)
            if total == 0:
                return None, None
        detail = await self._client.get_json(f"acts/{self.key}/{year}/{total}", none_on_no_content=True)
        if detail is None:
            return None, None
        if not isinstance(detail, dict):
            raise SejmApiError(
                f"Dziennik {self.key} zwrócił szczegóły aktu w nieoczekiwanym kształcie.", source=self.key
            )
        published = normalise.to_date(normalise.camelise(detail).get("promulgation"))
        return (date.fromisoformat(published) if published else None), f"{year}/{total}"

    async def measure(self, today: date) -> SourceCoverage:
        """Take the three-request measurement of this journal.

        Raises:
            SejmApiError: The host answered in a shape without years or without a count.
            EgressError: The host failed; the registry then uses `fallback`.
        """
        listing = normalise.camelise(await self._client.get_json("acts"))
        entries = [entry for entry in listing if isinstance(entry, dict)] if isinstance(listing, list) else []
        # Every host answers for its own journal only, so a lone entry is this one even under another code.
        entry = next(
            (item for item in entries if item.get("code") == self.key), entries[0] if len(entries) == 1 else None
        )
        raw_years: Any = entry.get("years") if entry is not None else None
        years = (
            tuple(sorted({year for year in raw_years if _int(year) is not None})) if isinstance(raw_years, list) else ()
        )
        if entry is None or not years:
            raise SejmApiError(f"Dziennik {self.key} nie podał roczników.", source=self.key)
        newest, newest_ref = await self._newest(years)
        count = _int(entry.get("deedsCount"))
        if count is None:
            count = _int(entry.get("count"))
        segment = CoverageSegment(
            key=self.key,
            label=self._journal.short_name,
            newest=newest,
            newest_ref=newest_ref,
            count=count,
            years=years,
        )
        return SourceCoverage(source=self.source, segments=(segment,), measured_on=today, origin="measured")

    def fallback(self) -> SourceCoverage:
        """The newest act recorded on 2026-11-05/06, without years and without touching the network.

        Raises:
            KeyError: The journal has no recorded value; the registry then skips it.
        """
        newest, newest_ref = NEWEST_RECORDED[self.key]
        segment = CoverageSegment(
            key=self.key,
            label=self._journal.short_name,
            newest=newest,
            newest_ref=newest_ref,
            count=None,
            years=None,
        )
        return SourceCoverage(
            source=self.source, segments=(segment,), measured_on=FALLBACK_MEASURED_ON, origin="fallback"
        )
