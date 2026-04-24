"""Tests du TTLCache et de hash_key."""

from __future__ import annotations

import asyncio

import pytest

from bot.cache import TTLCache, hash_key


async def test_set_then_get_returns_value() -> None:
    cache = TTLCache(max_size=10, ttl_sec=60.0)
    await cache.set("a", "value")
    assert await cache.get("a") == "value"


async def test_missing_key_returns_none() -> None:
    cache = TTLCache(max_size=10, ttl_sec=60.0)
    assert await cache.get("missing") is None


async def test_entry_expires_after_ttl() -> None:
    cache = TTLCache(max_size=10, ttl_sec=0.05)
    await cache.set("a", 1)
    await asyncio.sleep(0.08)
    assert await cache.get("a") is None


async def test_lru_evicts_oldest() -> None:
    cache = TTLCache(max_size=2, ttl_sec=60.0)
    await cache.set("a", 1)
    await cache.set("b", 2)
    await cache.set("c", 3)  # doit évincer "a"
    assert await cache.get("a") is None
    assert await cache.get("b") == 2
    assert await cache.get("c") == 3


async def test_get_updates_lru_order() -> None:
    cache = TTLCache(max_size=2, ttl_sec=60.0)
    await cache.set("a", 1)
    await cache.set("b", 2)
    await cache.get("a")  # "a" redevient la plus récente
    await cache.set("c", 3)  # doit évincer "b", pas "a"
    assert await cache.get("a") == 1
    assert await cache.get("b") is None


async def test_clear_empties_cache() -> None:
    cache = TTLCache(max_size=10, ttl_sec=60.0)
    await cache.set("a", 1)
    await cache.clear()
    assert await cache.get("a") is None


async def test_stats_track_hits_and_misses() -> None:
    cache = TTLCache(max_size=10, ttl_sec=60.0)
    await cache.set("a", 1)
    await cache.get("a")
    await cache.get("missing")
    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1


def test_hash_key_stable_on_same_input() -> None:
    k1 = hash_key("llm", "gemma", [{"role": "user", "content": "salut"}])
    k2 = hash_key("llm", "gemma", [{"role": "user", "content": "salut"}])
    assert k1 == k2


def test_hash_key_differs_on_different_input() -> None:
    k1 = hash_key("llm", "gemma", "a")
    k2 = hash_key("llm", "gemma", "b")
    assert k1 != k2


def test_invalid_ttl_raises() -> None:
    with pytest.raises(ValueError):
        TTLCache(max_size=10, ttl_sec=0)


def test_invalid_max_size_raises() -> None:
    with pytest.raises(ValueError):
        TTLCache(max_size=0, ttl_sec=60.0)
