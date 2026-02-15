"""In-memory store for search/browse results with filtering capabilities."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from law_scrapper_mcp.models.tool_outputs import ActSummaryOutput

logger = logging.getLogger(__name__)


@dataclass
class StoredResultSet:
    """A stored set of search/browse results."""

    result_set_id: str
    results: list[ActSummaryOutput]
    query_summary: str
    total_count: int
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)


@dataclass
class FilterHit:
    """A single result matching the filter criteria."""

    act: ActSummaryOutput
    matched_field: str
    matched_value: str


class ResultStore:
    """In-memory store for search/browse results with grep-like filtering."""

    def __init__(self, max_sets: int = 20, ttl: int = 3600):
        self._store: dict[str, StoredResultSet] = {}
        self._max_sets = max_sets
        self._ttl = ttl
        self._counter = 0
        self._lock = asyncio.Lock()

    async def store(
        self,
        results: list[ActSummaryOutput],
        query_summary: str,
        total_count: int,
    ) -> str:
        """Store a result set and return its ID."""
        async with self._lock:
            self._evict_expired()

            if len(self._store) >= self._max_sets:
                self._evict_lru()

            self._counter += 1
            result_set_id = f"rs_{self._counter}"

            self._store[result_set_id] = StoredResultSet(
                result_set_id=result_set_id,
                results=results,
                query_summary=query_summary,
                total_count=total_count,
            )
            logger.info(
                f"Stored result set {result_set_id}: {len(results)} results "
                f"(query: {query_summary})"
            )
            return result_set_id

    async def get(self, result_set_id: str) -> StoredResultSet | None:
        """Get a stored result set by ID."""
        async with self._lock:
            rs = self._store.get(result_set_id)
            if rs is None:
                return None
            if time.time() - rs.last_accessed > self._ttl:
                del self._store[result_set_id]
                return None
            rs.last_accessed = time.time()
            return rs

    async def list_sets(self) -> list[dict[str, Any]]:
        """List all available result sets."""
        async with self._lock:
            self._evict_expired()
            return [
                {
                    "result_set_id": rs.result_set_id,
                    "query_summary": rs.query_summary,
                    "result_count": len(rs.results),
                    "total_count": rs.total_count,
                    "created_at": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(rs.created_at)
                    ),
                }
                for rs in self._store.values()
            ]

    async def filter_results(
        self,
        result_set_id: str,
        *,
        pattern: str | None = None,
        field: str = "title",
        type_equals: str | None = None,
        status_equals: str | None = None,
        year_equals: int | None = None,
        date_field: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        sort_by: str | None = None,
        sort_desc: bool = False,
        limit: int | None = None,
    ) -> tuple[list[ActSummaryOutput], int]:
        """Filter a stored result set. Returns (filtered_results, original_count)."""
        rs = await self.get(result_set_id)
        if rs is None:
            raise ResultSetNotFoundError(result_set_id)

        filtered = list(rs.results)
        original_count = len(filtered)

        # Exact match filters
        if type_equals is not None:
            filtered = [r for r in filtered if r.type and r.type == type_equals]

        if status_equals is not None:
            filtered = [r for r in filtered if r.status == status_equals]

        if year_equals is not None:
            filtered = [r for r in filtered if r.year == year_equals]

        # Regex pattern filter
        if pattern is not None:
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern: {e}") from e

            filtered = [
                r
                for r in filtered
                if _match_field(r, field, compiled)
            ]

        # Date range filter
        if date_field and (date_from or date_to):
            filtered = _filter_by_date(filtered, date_field, date_from, date_to)

        # Sorting
        if sort_by:
            filtered = _sort_results(filtered, sort_by, sort_desc)

        # Limit
        if limit is not None and limit > 0:
            filtered = filtered[:limit]

        return filtered, original_count

    def _evict_expired(self) -> None:
        """Remove expired result sets (called under lock)."""
        now = time.time()
        expired = [
            k for k, v in self._store.items() if now - v.last_accessed > self._ttl
        ]
        for key in expired:
            del self._store[key]

    def _evict_lru(self) -> None:
        """Remove least recently used result set (called under lock)."""
        if not self._store:
            return
        lru_key = min(self._store, key=lambda k: self._store[k].last_accessed)
        logger.info(f"Evicting LRU result set: {lru_key}")
        del self._store[lru_key]


class ResultSetNotFoundError(Exception):
    """Raised when a result set is not found."""

    def __init__(self, result_set_id: str):
        self.result_set_id = result_set_id
        super().__init__(
            f"Zestaw wyników '{result_set_id}' nie istnieje lub wygasł. "
            f"Wykonaj wyszukiwanie, a następnie użyj zwróconego result_set_id."
        )


# --- Helper functions ---


_SEARCHABLE_FIELDS = {"title", "eli", "status", "type", "publisher"}
_DATE_FIELDS = {"promulgation_date", "effective_date"}
_SORTABLE_FIELDS = {"title", "eli", "year", "pos", "status", "type", "promulgation_date", "effective_date"}


def _match_field(act: ActSummaryOutput, field: str, compiled: re.Pattern[str]) -> bool:
    """Check if a field value matches the compiled regex."""
    if field not in _SEARCHABLE_FIELDS:
        # Default to searching title
        field = "title"

    value = getattr(act, field, None)
    if value is None:
        return False
    return compiled.search(str(value)) is not None


def _filter_by_date(
    results: list[ActSummaryOutput],
    date_field: str,
    date_from: str | None,
    date_to: str | None,
) -> list[ActSummaryOutput]:
    """Filter results by date range."""
    if date_field not in _DATE_FIELDS:
        return results

    filtered = []
    for r in results:
        value = getattr(r, date_field, None)
        if value is None:
            continue
        if date_from and value < date_from:
            continue
        if date_to and value > date_to:
            continue
        filtered.append(r)
    return filtered


def _sort_results(
    results: list[ActSummaryOutput],
    sort_by: str,
    desc: bool,
) -> list[ActSummaryOutput]:
    """Sort results by a field."""
    if sort_by not in _SORTABLE_FIELDS:
        return results

    def key_fn(r: ActSummaryOutput) -> Any:
        val = getattr(r, sort_by, None)
        if val is None:
            return "" if isinstance(getattr(ActSummaryOutput, sort_by, ""), str) else 0
        return val

    return sorted(results, key=key_fn, reverse=desc)
