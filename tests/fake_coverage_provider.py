"""An in-memory `CoverageProvider` for the coverage tests: it counts measurements and never touches the network."""

from __future__ import annotations

import asyncio
from datetime import date

from law_scrapper_mcp.services.coverage import CoverageSegment, SourceCoverage

MEASURED_ON = date(2026, 11, 8)


def segment(
    key: str = "DU",
    *,
    label: str = "Dziennik Ustaw",
    newest: date | None = date(2026, 11, 6),
    newest_ref: str | None = "2026/5811",
    count: int | None = 1234,
    years: tuple[int, ...] | None = (2024, 2025, 2026),
) -> CoverageSegment:
    """A segment with plausible defaults; override what a test cares about."""
    return CoverageSegment(key=key, label=label, newest=newest, newest_ref=newest_ref, count=count, years=years)


class FakeCoverageProvider:
    """Answers with fixed segments; `measure` can be slowed, failed and counted, `fallback` can fail too."""

    def __init__(
        self,
        key: str = "DU",
        *,
        source: str = "fake-source",
        segments: tuple[CoverageSegment, ...] | None = None,
        fail_measure: bool = False,
        fail_fallback: bool = False,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.key = key
        self.source = source
        self.segments = segments if segments is not None else (segment(key),)
        self.fail_measure = fail_measure
        self.fail_fallback = fail_fallback
        self.gate = gate
        self.measure_calls: list[date] = []
        self.fallback_calls = 0

    async def measure(self, today: date) -> SourceCoverage:
        self.measure_calls.append(today)
        if self.gate is not None:
            await self.gate.wait()
        if self.fail_measure:
            raise RuntimeError("host down")
        return SourceCoverage(source=self.source, segments=self.segments, measured_on=today, origin="measured")

    def fallback(self) -> SourceCoverage:
        self.fallback_calls += 1
        if self.fail_fallback:
            raise RuntimeError("no fallback")
        return SourceCoverage(source=self.source, segments=self.segments, measured_on=MEASURED_ON, origin="fallback")


class Clock:
    """A hand-driven pair of clocks for `CoverageRegistry(today=..., monotonic=...)`."""

    def __init__(self, today: date = MEASURED_ON) -> None:
        self.day = today
        self.seconds = 1_000.0

    def today(self) -> date:
        return self.day

    def monotonic(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds
