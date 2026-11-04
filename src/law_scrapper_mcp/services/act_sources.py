"""Pluggable sources of legal acts.

The core serves the Sejm's Dziennik Ustaw (`DU`) and Monitor Polski (`MP`) itself. A source module
can claim further publisher codes by registering an `ActSource`; the services then ask the registry
once per call and hand the request to the source instead of the Sejm client. With nothing
registered every service takes its original code path.

The protocol lives in `services/` because it returns `models` types and must be importable by the
services without a cycle through `source_modules`.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Protocol

from law_scrapper_mcp.client.exceptions import LawScrapperError
from law_scrapper_mcp.models.tool_outputs import ActDetailOutput, ActSummaryOutput

# Publisher codes the core serves through the Sejm client. A source cannot take them over.
RESERVED_PUBLISHERS: frozenset[str] = frozenset({"DU", "MP"})


class ActSourceConflictError(LawScrapperError):
    """A source tried to claim a publisher code or prefix that is reserved or already claimed."""


@dataclass(frozen=True, slots=True)
class SourceSearchQuery:
    """One search the services ask a source to run.

    Field names follow the tool parameters. `date_from`/`date_to` filter by the date an act entered
    into force, `pub_date_from`/`pub_date_to` by the date it was announced. A source that cannot
    honour a filter raises a Polish `ValueError` naming it, instead of ignoring the filter.

    Attributes:
        year: Publication year, if any.
        keywords: Keywords as the caller gave them.
        title: Substring of the title.
        act_type: Exact document type.
        date_from: Entry-into-force date, lower bound (`YYYY-MM-DD`).
        date_to: Entry-into-force date, upper bound.
        pub_date_from: Announcement date, lower bound.
        pub_date_to: Announcement date, upper bound.
        in_force: Filter on whether the act is in force.
        limit: Page size, 1..100 (the services enforce the range).
        offset: Number of records to skip.
    """

    year: int | None = None
    keywords: tuple[str, ...] = ()
    title: str | None = None
    act_type: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    pub_date_from: str | None = None
    pub_date_to: str | None = None
    in_force: bool | None = None
    limit: int = 20
    offset: int = 0


@dataclass(frozen=True, slots=True)
class SourceSearchPage:
    """One page of a source's search answer.

    Attributes:
        items: The records of the requested window, already in the tool output shape.
        total_count: Size of the whole answer, not of this page.
    """

    items: list[ActSummaryOutput]
    total_count: int


class ActSource(Protocol):
    """What a source of acts must answer.

    Raises, by convention: `ValueError` (Polish text) for an input the source cannot honour,
    `ActNotFoundError` when the act does not exist, and the `ApiUnavailableError` family with
    `source=` set when the host fails.
    """

    name: str
    """Label of the egress host the source talks to; used in messages."""

    async def search(self, publisher: str, query: SourceSearchQuery) -> SourceSearchPage:
        """Run a search and return the requested window."""
        ...

    async def get_details(self, publisher: str, year: int, pos: int) -> ActDetailOutput:
        """Return the metadata of one act; content flags and the table of contents stay default."""
        ...

    async def get_relationships(self, publisher: str, year: int, pos: int) -> dict[str, list[Any]]:
        """Return the relations of one act under the source's own keys; `{}` for none."""
        ...

    async def get_pdf(self, publisher: str, year: int, pos: int, *, max_bytes: int) -> bytes:
        """Download the act's PDF, aborting once the body passes `max_bytes`."""
        ...

    def pdf_url(self, publisher: str, year: int, pos: int) -> str:
        """Public address of the act's PDF."""
        ...


class ActSourceRegistry:
    """Who serves which publisher code.

    Lookup is exact code first, then prefix (longest prefix wins). Registration happens in the
    server lifespan before the first request and unregistration at shutdown, with no `await`
    in between, so a plain dict is enough: no lock is needed.
    """

    def __init__(self) -> None:
        self._by_code: dict[str, ActSource] = {}
        self._by_prefix: dict[str, ActSource] = {}

    def register(
        self,
        source: ActSource,
        *,
        publishers: Collection[str],
        prefixes: Collection[str] = (),
    ) -> None:
        """Claim publisher codes and prefixes for `source`.

        Raises:
            ActSourceConflictError: A code is `DU` or `MP`, a code or prefix is already claimed, or
                a prefix would swallow a reserved code. Nothing is registered in that case.
        """
        codes = list(publishers)
        prefix_list = list(prefixes)
        for code in codes:
            if code in RESERVED_PUBLISHERS:
                raise ActSourceConflictError(
                    f"Kod wydawcy {code} jest obsługiwany przez rdzeń i nie może być przejęty."
                )
            if code in self._by_code:
                raise ActSourceConflictError(
                    f"Kod wydawcy {code} jest już zajęty przez źródło {self._by_code[code].name}."
                )
        for prefix in prefix_list:
            if not prefix:
                raise ActSourceConflictError("Prefiks kodu wydawcy nie może być pusty.")
            if any(reserved.startswith(prefix) for reserved in RESERVED_PUBLISHERS):
                raise ActSourceConflictError(
                    f"Prefiks {prefix} obejmuje kod obsługiwany przez rdzeń ({', '.join(sorted(RESERVED_PUBLISHERS))})."
                )
            if prefix in self._by_prefix:
                raise ActSourceConflictError(
                    f"Prefiks {prefix} jest już zajęty przez źródło {self._by_prefix[prefix].name}."
                )
        for code in codes:
            self._by_code[code] = source
        for prefix in prefix_list:
            self._by_prefix[prefix] = source

    def unregister(self, source: ActSource) -> None:
        """Release every code and prefix `source` holds. Unknown sources are ignored."""
        self._by_code = {code: held for code, held in self._by_code.items() if held is not source}
        self._by_prefix = {prefix: held for prefix, held in self._by_prefix.items() if held is not source}

    def for_publisher(self, code: str) -> ActSource | None:
        """Return the source serving `code`, or `None` when the Sejm path applies."""
        source = self._by_code.get(code)
        if source is not None:
            return source
        for prefix in sorted(self._by_prefix, key=len, reverse=True):
            if code.startswith(prefix):
                return self._by_prefix[prefix]
        return None

    def publishers(self) -> tuple[str, ...]:
        """Exact publisher codes currently registered, sorted."""
        return tuple(sorted(self._by_code))
