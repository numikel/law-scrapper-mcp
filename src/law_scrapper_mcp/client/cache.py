"""TTL cache implementation for API responses."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    """Cache entry with TTL and creation time."""

    value: T
    expires_at: float
    created_at: float
    namespace: str | None = None


class TTLCache:
    """Async-safe TTL cache with oldest-first eviction.

    Entries may carry a namespace (the origin of the host they came from). The cache stays one
    shared pool with one cap, but when it is full the namespace holding the most entries pays
    first, so a chatty host evicts itself before it touches a quiet host's entries. Every
    namespace is therefore guaranteed `max_entries // (number of namespaces holding entries)`
    entries without any further setting. Entries stored without a namespace form one group of
    their own, and a cache that only ever sees one group evicts exactly as it always did.
    """

    def __init__(self, max_entries: int = 1000):
        self._cache: dict[str, CacheEntry[Any]] = {}
        # Entries per namespace, kept in step with `_cache` by `_remove` and `set`.
        self._counts: dict[str | None, int] = {}
        self._max_entries = max_entries
        self._lock = asyncio.Lock()

    async def get(self, key: str, max_age: float | None = None) -> Any | None:
        """Get value from cache if not expired.

        `max_age` bounds freshness for this read: an entry older than it is a miss
        for the caller even though it stays valid for callers with a longer TTL.
        Entries are keyed by request, not by caller, so two services asking the
        same question share one entry (D8) — this keeps the sharing while letting
        the shorter TTL still mean what its setting says.
        """
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None

            now = time.time()
            if now > entry.expires_at:
                self._remove(key)
                return None
            if max_age is not None and now - entry.created_at > max_age:
                return None

            return entry.value

    async def set(self, key: str, value: Any, ttl: int, *, namespace: str | None = None) -> None:
        """Set value in cache with TTL.

        `namespace` says whose entry this is. It matters only when the cache is full: see the
        class docstring. Reads need no namespace, because the entry remembers its own.
        """
        async with self._lock:
            now = time.time()
            self._remove(key)  # an overwrite must not count twice
            self._cache[key] = CacheEntry(value=value, expires_at=now + ttl, created_at=now, namespace=namespace)
            self._counts[namespace] = self._counts.get(namespace, 0) + 1

            # Evict if over capacity
            if len(self._cache) > self._max_entries:
                self._evict_expired()
                while len(self._cache) > self._max_entries:
                    self._evict_oldest_of_largest_namespace(inserting=namespace)

    async def delete(self, key: str) -> None:
        """Delete entry from cache."""
        async with self._lock:
            self._remove(key)

    async def clear(self) -> None:
        """Clear all entries from cache."""
        async with self._lock:
            self._cache.clear()
            self._counts.clear()

    def _remove(self, key: str) -> None:
        """Drop one entry and keep the per-namespace count right (called under lock)."""
        entry = self._cache.pop(key, None)
        if entry is None:
            return
        remaining = self._counts[entry.namespace] - 1
        if remaining:
            self._counts[entry.namespace] = remaining
        else:
            del self._counts[entry.namespace]

    def _evict_expired(self) -> None:
        """Remove all expired entries (called under lock)."""
        now = time.time()
        expired_keys = [k for k, v in self._cache.items() if now > v.expires_at]
        for key in expired_keys:
            self._remove(key)

    def _evict_oldest_of_largest_namespace(self, inserting: str | None) -> None:
        """Make room by dropping the oldest 10% of the namespace holding the most entries.

        A tie goes to the namespace being inserted into, so the writer pays for its own growth.
        The batch is 10% of that namespace (at least one entry), which with a single namespace
        is exactly the 10% of the whole cache this method has always dropped (called under lock).
        """
        victim = max(self._counts, key=lambda ns: (self._counts[ns], ns == inserting))
        oldest_first = sorted(
            (item for item in self._cache.items() if item[1].namespace == victim),
            key=lambda item: item[1].created_at,
        )
        for key, _ in oldest_first[: max(1, self._counts[victim] // 10)]:
            self._remove(key)

    @property
    def size(self) -> int:
        """Get current cache size."""
        return len(self._cache)
