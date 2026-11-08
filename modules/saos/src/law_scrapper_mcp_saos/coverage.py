"""Coverage of SAOS: one provider with a segment per court type, and the rule that says when a search hits a gap.

SAOS holds judgments of five court types and the newest record of each is very different: common courts
are current, the Supreme Court stops in 2016, the Constitutional Tribunal in 2015, the National Appeal
Chamber in 2018, and the administrative courts are not in the service at all. A measurement therefore takes
one request per court type (`pageSize=1`, newest first, with the upper date bound that keeps the typo dates
of the future out), and the segment's `count` and `newest` come from that one page.

`fallback` holds the figures recorded on 2026-11-02 (`tests/fixtures/saos/PROVENANCE.md`) as constants: the
container image carries no tests, so a runtime read of a recording would fail exactly where the fallback is
needed. `modules/saos/tests/test_saos_coverage.py` ties every constant to the recording it came from.
"""

from __future__ import annotations

from datetime import date, timedelta

from law_scrapper_mcp.services.coverage import (
    FALLBACK_NOTE,
    STALE_AFTER_DAYS,
    CoverageOrigin,
    CoverageSegment,
    SourceCoverage,
)
from law_scrapper_mcp_saos.client import SaosClient
from law_scrapper_mcp_saos.mapping import COURT_TYPES, SOURCE
from law_scrapper_mcp_saos.models import SaosSearchPage

__all__ = [
    "ADMINISTRATIVE",
    "ADMINISTRATIVE_MESSAGE",
    "COURT_LABELS",
    "COVERAGE_KEY",
    "FALLBACK_MEASURED_ON",
    "SaosCoverageProvider",
    "court_gap_message",
]

# The registry key of the one SAOS provider (the core's registry is keyed per provider, not per segment).
COVERAGE_KEY = SOURCE
ADMINISTRATIVE = "ADMINISTRATIVE"

# Polish names of the court types, as the agent reads them in a hint and in `get_system_metadata`.
COURT_LABELS: dict[str, str] = {
    "COMMON": "sądy powszechne",
    "SUPREME": "Sąd Najwyższy",
    "CONSTITUTIONAL_TRIBUNAL": "Trybunał Konstytucyjny",
    "NATIONAL_APPEAL_CHAMBER": "Krajowa Izba Odwoławcza",
    ADMINISTRATIVE: "sądy administracyjne",
}
# Court types whose label is plural, for the verb of the hint.
_PLURAL = frozenset({"COMMON", ADMINISTRATIVE})

# The day the fallback figures were measured (recordings of 2026-11-02).
FALLBACK_MEASURED_ON = date(2026, 11, 2)
_FALLBACK_SEGMENTS: tuple[tuple[str, date | None, int], ...] = (
    ("COMMON", date(2026, 10, 27), 474_859),
    ("SUPREME", date(2016, 6, 22), 38_081),
    ("CONSTITUTIONAL_TRIBUNAL", date(2015, 12, 9), 9_503),
    ("NATIONAL_APPEAL_CHAMBER", date(2018, 9, 6), 22_168),
    (ADMINISTRATIVE, None, 0),
)

ADMINISTRATIVE_MESSAGE = "SAOS nie zawiera orzeczeń sądów administracyjnych."
_NOT_PROOF = "Brak wyniku w SAOS nie dowodzi, że orzeczenie nie istnieje."


def _segment(court_type: str, page: SaosSearchPage) -> CoverageSegment:
    """One segment from the newest-first page of a court type: its size and its newest record."""
    newest: date | None = None
    newest_ref: str | None = None
    if page.items:
        item = page.items[0]
        try:
            newest = date.fromisoformat(item.judgment_date) if item.judgment_date else None
        except ValueError:
            newest = None
        newest_ref = next((case.case_number for case in item.court_cases if case.case_number), None)
    return CoverageSegment(
        key=court_type,
        label=COURT_LABELS[court_type],
        newest=newest,
        newest_ref=newest_ref,
        count=page.info.total_results,
        years=None,
    )


class SaosCoverageProvider:
    """Coverage of SAOS over the module's own client; registered under `COVERAGE_KEY`."""

    key = COVERAGE_KEY
    source = SOURCE

    def __init__(self, client: SaosClient) -> None:
        self._client = client

    async def measure(self, today: date) -> SourceCoverage:
        """Ask SAOS for the newest judgment and the size of every court type: five requests, one after another.

        The upper bound `today` is mandatory: without it the newest-first order starts with records dated
        in the future (typos such as 3013-12-04). Pages are cached by the client under the module's search
        TTL like any search; no search of the tools has these parameters (they always send `pageNumber`), so
        a measurement never serves a page that was fetched for another purpose.

        Raises:
            SejmApiError, EgressError: SAOS failed or answered in an unexpected shape; the registry then
                uses `fallback`.
        """
        segments: list[CoverageSegment] = []
        for court_type in COURT_TYPES:
            page = await self._client.search(
                {
                    "pageSize": 1,
                    "courtType": court_type,
                    "judgmentDateTo": today.isoformat(),
                    "sortingField": "JUDGMENT_DATE",
                    "sortingDirection": "DESC",
                }
            )
            segments.append(_segment(court_type, page))
        return SourceCoverage(source=self.source, segments=tuple(segments), measured_on=today, origin="measured")

    def fallback(self) -> SourceCoverage:
        """The figures measured on 2026-11-02, without touching the network."""
        segments = tuple(
            CoverageSegment(key=court_type, label=COURT_LABELS[court_type], newest=newest, count=count)
            for court_type, newest, count in _FALLBACK_SEGMENTS
        )
        return SourceCoverage(
            source=self.source, segments=segments, measured_on=FALLBACK_MEASURED_ON, origin="fallback"
        )


def court_gap_message(
    segment: CoverageSegment, *, date_to: date, origin: CoverageOrigin, measured_on: date
) -> str | None:
    """The Polish hint when a search of one court type reaches past what SAOS holds for it; else `None`.

    The upper bound of the search (`date_to`, already clamped to today) must lie more than
    `STALE_AFTER_DAYS` past the segment's newest judgment: exactly 30 days past is not a gap, 31 is. A
    segment without a newest date makes no claim. A court type the measurement counts as empty (today the
    administrative courts) gets `ADMINISTRATIVE_MESSAGE` from the service instead, never this sentence.
    """
    if segment.newest is None or date_to <= segment.newest + timedelta(days=STALE_AFTER_DAYS):
        return None
    label = segment.label[:1].upper() + segment.label[1:]
    verb = "sięgają" if segment.key in _PLURAL else "sięga"
    message = (
        f"{label} w SAOS {verb} do {segment.newest} (pomiar z {measured_on}); orzeczeń z późniejszych dat "
        f"w tym źródle nie ma. {_NOT_PROOF}"
    )
    return f"{message} {FALLBACK_NOTE}" if origin == "fallback" else message
