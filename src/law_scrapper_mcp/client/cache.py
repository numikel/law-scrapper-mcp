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


class TTLCache:
    """Async-safe TTL cache with LRU eviction."""

    def __init__(self, max_entries: int = 1000):
        self._cache: dict[str, CacheEntry[Any]] = {}
        self._max_entries = max_entries
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        """Get value from cache if not expired."""
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None

            if time.time() > entry.expires_at:
                del self._cache[key]
                return None

            return entry.value

    async def set(self, key: str, value: Any, ttl: int) -> None:
        """Set value in cache with TTL."""
        async with self._lock:
            now = time.time()
            self._cache[key] = CacheEntry(
                value=value, expires_at=now + ttl, created_at=now
            )

            # Evict if over capacity
            if len(self._cache) > self._max_entries:
                self._evict_expired()
                if len(self._cache) > self._max_entries:
                    self._evict_lru()

    async def delete(self, key: str) -> None:
        """Delete entry from cache."""
        async with self._lock:
            self._cache.pop(key, None)

    async def clear(self) -> None:
        """Clear all entries from cache."""
        async with self._lock:
            self._cache.clear()

    def _evict_expired(self) -> None:
        """Remove all expired entries (called under lock)."""
        now = time.time()
        expired_keys = [k for k, v in self._cache.items() if now > v.expires_at]
        for key in expired_keys:
            del self._cache[key]

    def _evict_lru(self) -> None:
        """Remove least recently used entries (called under lock)."""
        # Sort by creation time and remove oldest 10%
        sorted_items = sorted(self._cache.items(), key=lambda x: x[1].created_at)
        num_to_remove = max(1, len(self._cache) // 10)
        for key, _ in sorted_items[:num_to_remove]:
            del self._cache[key]

    @property
    def size(self) -> int:
        """Get current cache size."""
        return len(self._cache)
