"""Tests for TTLCache implementation."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

from law_scrapper_mcp.client.cache import TTLCache


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
