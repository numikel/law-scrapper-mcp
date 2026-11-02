"""Tests for TTLCache implementation."""

from __future__ import annotations

import asyncio
import random
import time
from unittest.mock import patch

import pytest

from law_scrapper_mcp.client.cache import TTLCache

pytestmark = pytest.mark.asyncio


class TestTTLCacheBasicOperations:
    """Tests for basic cache operations."""

    async def test_get_set_basic(self, cache: TTLCache):
        """Test basic get and set operations."""
        await cache.set("key1", "value1", ttl=60)
        assert await cache.get("key1") == "value1"

    async def test_get_nonexistent_key(self, cache: TTLCache):
        """Test getting a key that doesn't exist."""
        assert await cache.get("nonexistent") is None

    async def test_set_overwrites_existing(self, cache: TTLCache):
        """Test that set overwrites existing values."""
        await cache.set("key1", "value1", ttl=60)
        await cache.set("key1", "value2", ttl=60)
        assert await cache.get("key1") == "value2"

    async def test_delete_key(self, cache: TTLCache):
        """Test deleting a key."""
        await cache.set("key1", "value1", ttl=60)
        await cache.delete("key1")
        assert await cache.get("key1") is None

    async def test_delete_nonexistent_key(self, cache: TTLCache):
        """Test deleting a key that doesn't exist."""
        await cache.delete("nonexistent")  # Should not raise
        assert await cache.get("nonexistent") is None

    async def test_clear(self, cache: TTLCache):
        """Test clearing all entries."""
        await cache.set("key1", "value1", ttl=60)
        await cache.set("key2", "value2", ttl=60)
        await cache.clear()
        assert await cache.get("key1") is None
        assert await cache.get("key2") is None
        assert cache.size == 0

    async def test_size_property(self, cache: TTLCache):
        """Test the size property."""
        assert cache.size == 0
        await cache.set("key1", "value1", ttl=60)
        assert cache.size == 1
        await cache.set("key2", "value2", ttl=60)
        assert cache.size == 2
        await cache.delete("key1")
        assert cache.size == 1


class TestTTLExpiration:
    """Tests for TTL expiration behavior."""

    async def test_expired_entry_returns_none(self, cache: TTLCache):
        """Test that expired entries return None."""
        await cache.set("key1", "value1", ttl=1)
        time.sleep(1.1)
        assert await cache.get("key1") is None

    async def test_expired_entry_removed_from_cache(self, cache: TTLCache):
        """Test that expired entries are removed from internal storage."""
        await cache.set("key1", "value1", ttl=1)
        initial_size = cache.size
        time.sleep(1.1)
        await cache.get("key1")  # This should trigger cleanup
        assert cache.size < initial_size

    async def test_not_yet_expired_entry(self, cache: TTLCache):
        """Test that entries within TTL are still accessible."""
        await cache.set("key1", "value1", ttl=5)
        time.sleep(1)
        assert await cache.get("key1") == "value1"

    async def test_ttl_with_mock_time(self, cache: TTLCache):
        """Test TTL expiration using mocked time."""
        with patch("time.time") as mock_time:
            mock_time.return_value = 1000.0
            await cache.set("key1", "value1", ttl=60)

            # Still within TTL
            mock_time.return_value = 1050.0
            assert await cache.get("key1") == "value1"

            # Expired
            mock_time.return_value = 1061.0
            assert await cache.get("key1") is None


class TestLRUEviction:
    """Tests for LRU eviction when max_entries is exceeded."""

    async def test_lru_eviction_when_full(self):
        """Test that LRU eviction occurs when cache is full."""
        small_cache = TTLCache(max_entries=5)

        # Fill the cache
        for i in range(5):
            await small_cache.set(f"key{i}", f"value{i}", ttl=3600)

        assert small_cache.size == 5

        # Add one more to trigger eviction
        await small_cache.set("key5", "value5", ttl=3600)

        # Should still be at or below max size
        assert small_cache.size <= 5

        # The oldest entry should be evicted (key0)
        assert await small_cache.get("key0") is None

    async def test_lru_evicts_oldest_created(self):
        """Test that LRU evicts the oldest created entries."""
        small_cache = TTLCache(max_entries=3)

        with patch("time.time") as mock_time:
            mock_time.return_value = 1000.0
            await small_cache.set("key1", "value1", ttl=3600)

            mock_time.return_value = 1010.0
            await small_cache.set("key2", "value2", ttl=3600)

            mock_time.return_value = 1020.0
            await small_cache.set("key3", "value3", ttl=3600)

            # Trigger eviction
            mock_time.return_value = 1030.0
            await small_cache.set("key4", "value4", ttl=3600)

            # key1 (oldest) should be evicted
            assert await small_cache.get("key1") is None
            assert await small_cache.get("key2") is not None

    async def test_eviction_clears_expired_first(self):
        """Test that expired entries are cleared before LRU eviction."""
        small_cache = TTLCache(max_entries=3)

        with patch("time.time") as mock_time:
            mock_time.return_value = 1000.0
            await small_cache.set("key1", "value1", ttl=10)  # Will expire soon

            mock_time.return_value = 1005.0
            await small_cache.set("key2", "value2", ttl=3600)

            mock_time.return_value = 1010.0
            await small_cache.set("key3", "value3", ttl=3600)

            # key1 is now expired
            mock_time.return_value = 1015.0
            await small_cache.set("key4", "value4", ttl=3600)

            # Expired key1 should be cleaned up, no LRU eviction needed
            assert await small_cache.get("key1") is None
            assert await small_cache.get("key2") is not None
            assert await small_cache.get("key3") is not None
            assert await small_cache.get("key4") is not None


class TestAsyncConcurrency:
    """Tests for async concurrency of cache operations."""

    async def test_concurrent_reads_and_writes(self, cache: TTLCache):
        """Test concurrent reads and writes from multiple async tasks."""

        async def writer(key: str, value: str):
            await cache.set(key, value, ttl=60)
            await asyncio.sleep(0.01)

        async def reader(key: str):
            await cache.get(key)
            await asyncio.sleep(0.01)

        # Create concurrent write tasks
        write_tasks = [writer(f"key{i}", f"value{i}") for i in range(20)]
        # Create concurrent read tasks
        read_tasks = [reader(f"key{i % 10}") for i in range(20)]

        # Run all tasks concurrently
        await asyncio.gather(*write_tasks, *read_tasks)

        # No exceptions should be raised
        assert cache.size > 0

    async def test_concurrent_size_queries(self, cache: TTLCache):
        """Test concurrent size property access."""

        async def check_size():
            size = cache.size
            assert size >= 0

        await cache.set("key1", "value1", ttl=60)

        # Run 50 concurrent size checks
        tasks = [check_size() for _ in range(50)]
        await asyncio.gather(*tasks)


class TestCacheEdgeCases:
    """Tests for edge cases."""

    async def test_zero_ttl(self, cache: TTLCache):
        """Test setting entry with zero TTL."""
        await cache.set("key1", "value1", ttl=0)
        # Should be immediately expired
        assert await cache.get("key1") is None

    async def test_negative_ttl(self, cache: TTLCache):
        """Test setting entry with negative TTL."""
        await cache.set("key1", "value1", ttl=-10)
        assert await cache.get("key1") is None

    async def test_very_large_ttl(self, cache: TTLCache):
        """Test setting entry with very large TTL."""
        await cache.set("key1", "value1", ttl=365 * 24 * 3600)  # 1 year
        assert await cache.get("key1") == "value1"

    async def test_cache_with_complex_values(self, cache: TTLCache):
        """Test caching complex data structures."""
        complex_value = {
            "list": [1, 2, 3],
            "dict": {"nested": "value"},
            "tuple": (1, 2),
        }
        await cache.set("complex", complex_value, ttl=60)
        assert await cache.get("complex") == complex_value

    async def test_empty_string_value(self, cache: TTLCache):
        """Test caching empty string."""
        await cache.set("empty", "", ttl=60)
        assert await cache.get("empty") == ""

    async def test_none_value(self, cache: TTLCache):
        """Test caching None value."""
        await cache.set("none_key", None, ttl=60)
        # Should return None, but it's cached (different from missing key)
        # This is a design choice - None is a valid cache value
        assert await cache.get("none_key") is None


class TestCallerBoundedAge:
    """A shared entry is fresh only within the reading caller's own TTL."""

    @pytest.mark.asyncio
    async def test_entry_older_than_max_age_is_a_miss_but_stays_for_longer_ttls(self):
        cache = TTLCache(max_entries=10)
        with patch("time.time") as mock_time:
            mock_time.return_value = 1000.0
            await cache.set("shared", "page", ttl=600)

            mock_time.return_value = 1400.0
            assert await cache.get("shared", max_age=300) is None
            assert await cache.get("shared") == "page"
            assert await cache.get("shared", max_age=600) == "page"


class _ReferenceCache:
    """The cache as it behaved before namespaces existed: one pool, the oldest 10% goes.

    A copy of the old algorithm, kept here so the equivalence test compares against a fixed
    point instead of against the code under test.
    """

    def __init__(self, max_entries: int) -> None:
        self.entries: dict[str, tuple[float, float]] = {}  # key -> (expires_at, created_at)
        self.max_entries = max_entries

    def set(self, key: str, ttl: int, now: float) -> None:
        self.entries[key] = (now + ttl, now)
        if len(self.entries) > self.max_entries:
            for k in [k for k, (expires_at, _) in self.entries.items() if now > expires_at]:
                del self.entries[k]
            if len(self.entries) > self.max_entries:
                ordered = sorted(self.entries.items(), key=lambda item: item[1][1])
                for k, _ in ordered[: max(1, len(self.entries) // 10)]:
                    del self.entries[k]


class TestNamespaceEviction:
    """One shared cap, but the namespace holding the most entries pays first (Q-7, variant B)."""

    @pytest.mark.parametrize("namespace", [None, "https://api.sejm.gov.pl"])
    async def test_a_single_group_evicts_exactly_as_before(self, namespace: str | None) -> None:
        rng = random.Random(20261102)
        cache = TTLCache(max_entries=20)
        reference = _ReferenceCache(max_entries=20)

        with patch("time.time") as mock_time:
            now = 1000.0
            for step in range(400):
                now += 1.0
                mock_time.return_value = now
                key = f"key{rng.randrange(60)}"
                ttl = rng.choice([5, 50, 5000])
                await cache.set(key, step, ttl, namespace=namespace)
                reference.set(key, ttl, now)

                assert set(cache._cache) == set(reference.entries), f"diverged at step {step}"

    async def test_a_chatty_namespace_evicts_only_itself(self) -> None:
        cache = TTLCache(max_entries=10)
        for i in range(3):
            await cache.set(f"quiet{i}", i, ttl=3600, namespace="quiet")

        for i in range(100):
            await cache.set(f"chatty{i}", i, ttl=3600, namespace="chatty")

        assert cache.size <= 10
        assert [await cache.get(f"quiet{i}") for i in range(3)] == [0, 1, 2]

    async def test_a_namespace_keeps_its_fair_share_against_a_much_larger_one(self) -> None:
        cache = TTLCache(max_entries=10)
        for i in range(5):
            await cache.set(f"quiet{i}", i, ttl=3600, namespace="quiet")

        for i in range(50):
            await cache.set(f"chatty{i}", i, ttl=3600, namespace="chatty")

        # Two namespaces on a cap of 10: each is guaranteed 10 // 2 = 5 entries.
        assert cache._counts["quiet"] == 5
        assert cache._counts["chatty"] == 5

    async def test_a_tie_is_paid_by_the_namespace_being_inserted_into(self) -> None:
        cache = TTLCache(max_entries=3)
        with patch("time.time") as mock_time:
            for step, (key, namespace) in enumerate([("a1", "A"), ("b1", "B"), ("b2", "B")]):
                mock_time.return_value = 1000.0 + step
                await cache.set(key, key, ttl=3600, namespace=namespace)

            mock_time.return_value = 1010.0
            await cache.set("a2", "a2", ttl=3600, namespace="A")  # A=2, B=2: a tie

        assert "a1" not in cache._cache
        assert "a2" in cache._cache
        assert "b1" in cache._cache
        assert "b2" in cache._cache

    async def test_the_oldest_entries_of_the_victim_go_first(self) -> None:
        cache = TTLCache(max_entries=4)
        with patch("time.time") as mock_time:
            for step, key in enumerate(["x1", "x2", "x3"]):
                mock_time.return_value = 1000.0 + step
                await cache.set(key, key, ttl=3600, namespace="X")
            mock_time.return_value = 1005.0
            await cache.set("y1", "y1", ttl=3600, namespace="Y")
            mock_time.return_value = 1006.0
            await cache.set("x4", "x4", ttl=3600, namespace="X")  # X=4, Y=1, total 5 > 4

        assert "x1" not in cache._cache
        assert "y1" in cache._cache

    async def test_expired_entries_are_dropped_before_any_namespace_pays(self) -> None:
        cache = TTLCache(max_entries=3)
        with patch("time.time") as mock_time:
            mock_time.return_value = 1000.0
            await cache.set("old", "old", ttl=10, namespace="A")
            await cache.set("a2", "a2", ttl=3600, namespace="A")
            await cache.set("b1", "b1", ttl=3600, namespace="B")

            mock_time.return_value = 1100.0
            await cache.set("b2", "b2", ttl=3600, namespace="B")

        assert "a2" in cache._cache
        assert "b1" in cache._cache
        assert "b2" in cache._cache

    async def test_entries_without_a_namespace_are_a_group_of_their_own(self) -> None:
        cache = TTLCache(max_entries=4)
        for i in range(2):
            await cache.set(f"plain{i}", i, ttl=3600)
        for i in range(10):
            await cache.set(f"ns{i}", i, ttl=3600, namespace="N")

        assert cache._counts[None] == 2
        assert cache._counts["N"] == 2


class TestNamespaceCounts:
    """The per-namespace counts must stay equal to what is really stored."""

    @staticmethod
    def actual_counts(cache: TTLCache) -> dict[str | None, int]:
        counts: dict[str | None, int] = {}
        for entry in cache._cache.values():
            counts[entry.namespace] = counts.get(entry.namespace, 0) + 1
        return counts

    async def test_overwrite_delete_and_clear(self) -> None:
        cache = TTLCache(max_entries=10)
        await cache.set("k1", 1, ttl=3600, namespace="A")
        await cache.set("k1", 2, ttl=3600, namespace="A")
        await cache.set("k2", 1, ttl=3600, namespace="A")
        await cache.set("k3", 1, ttl=3600, namespace="B")
        assert cache._counts == {"A": 2, "B": 1}

        await cache.delete("k1")
        await cache.delete("missing")
        assert cache._counts == {"A": 1, "B": 1}

        await cache.set("k3", 1, ttl=3600, namespace="A")  # same key, another namespace
        assert cache._counts == {"A": 2}

        await cache.clear()
        assert cache._counts == {}
        assert cache.size == 0

    async def test_expiry_on_get(self) -> None:
        cache = TTLCache(max_entries=10)
        with patch("time.time") as mock_time:
            mock_time.return_value = 1000.0
            await cache.set("k", 1, ttl=10, namespace="A")
            mock_time.return_value = 1100.0
            assert await cache.get("k") is None

        assert cache._counts == {}

    async def test_counts_survive_a_long_mixed_run(self) -> None:
        rng = random.Random(7)
        cache = TTLCache(max_entries=15)
        with patch("time.time") as mock_time:
            now = 1000.0
            for step in range(500):
                now += 1.0
                mock_time.return_value = now
                key = f"key{rng.randrange(40)}"
                action = rng.choice(["set", "set", "set", "get", "delete"])
                if action == "set":
                    await cache.set(key, step, rng.choice([3, 30, 3000]), namespace=rng.choice(["A", "B", "C", None]))
                elif action == "get":
                    await cache.get(key)
                else:
                    await cache.delete(key)

                assert cache._counts == self.actual_counts(cache), f"diverged at step {step}"
                assert cache.size <= 15
