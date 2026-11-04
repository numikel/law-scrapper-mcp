"""An in-memory `ActSource` for the seam tests: it records calls and never touches the network."""

from __future__ import annotations

from typing import Any

from law_scrapper_mcp.client.exceptions import ActNotFoundError
from law_scrapper_mcp.models.tool_outputs import ActDetailOutput, ActSummaryOutput
from law_scrapper_mcp.services.act_sources import SourceSearchPage, SourceSearchQuery

FAKE_PUBLISHER = "ZZ_TEST"


def summary(pos: int, publisher: str = FAKE_PUBLISHER, year: int = 2025) -> ActSummaryOutput:
    """A full-detail summary record, so detail-level trimming has something to remove."""
    return ActSummaryOutput(
        eli=f"{publisher}/{year}/{pos}",
        publisher=publisher,
        year=year,
        pos=pos,
        title=f"Akt {pos}",
        status="",
        type="Uchwała",
        promulgation_date="2025-02-03",
        effective_date="2025-03-01",
        in_force="true",
    )


class FakeActSource:
    """Serves `total` synthetic acts; every call is appended to `calls` as a tuple."""

    name = "FAKE_SOURCE"

    def __init__(
        self,
        *,
        total: int = 30,
        pdf: bytes = b"",
        has_pdf: bool = True,
        relations: dict[str, list[Any]] | None = None,
        missing: bool = False,
        pdf_missing: bool = False,
    ) -> None:
        self.total = total
        self.pdf = pdf
        self.has_pdf = has_pdf
        self.relations = relations if relations is not None else {"Akty zmieniane": [{"id": "x"}]}
        self.missing = missing
        self.pdf_missing = pdf_missing
        self.calls: list[tuple[Any, ...]] = []

    async def search(self, publisher: str, query: SourceSearchQuery) -> SourceSearchPage:
        self.calls.append(("search", publisher, query))
        start = query.offset
        stop = min(start + query.limit, self.total)
        return SourceSearchPage(
            items=[summary(n, publisher) for n in range(start + 1, stop + 1)], total_count=self.total
        )

    async def get_details(self, publisher: str, year: int, pos: int) -> ActDetailOutput:
        self.calls.append(("get_details", publisher, year, pos))
        if self.missing:
            raise ActNotFoundError(f"{publisher}/{year}/{pos}", source=self.name)
        return ActDetailOutput(
            eli=f"{publisher}/{year}/{pos}",
            publisher=publisher,
            year=year,
            pos=pos,
            title="Uchwała w sprawie budżetu",
            status="",
            has_pdf=self.has_pdf,
            has_html=False,
        )

    async def get_relationships(self, publisher: str, year: int, pos: int) -> dict[str, list[Any]]:
        self.calls.append(("get_relationships", publisher, year, pos))
        return self.relations

    async def get_pdf(self, publisher: str, year: int, pos: int, *, max_bytes: int) -> bytes:
        self.calls.append(("get_pdf", publisher, year, pos, max_bytes))
        if self.missing or self.pdf_missing:
            raise ActNotFoundError(f"{publisher}/{year}/{pos}", source=self.name)
        return self.pdf

    def pdf_url(self, publisher: str, year: int, pos: int) -> str:
        return f"https://fake.example/api/eli/acts/{publisher}/{year}/{pos}/text.pdf"
