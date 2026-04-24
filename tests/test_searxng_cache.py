"""Tests du cache dans SearxngClient."""

from __future__ import annotations

from unittest.mock import AsyncMock

from bot.search import searxng as searxng_module
from bot.search.searxng import SearxngClient


def _fake_payload() -> dict[str, object]:
    return {
        "results": [
            {"title": "Article 1", "url": "http://ex/1", "content": "snippet 1"},
            {"title": "Article 2", "url": "http://ex/2", "content": "snippet 2"},
        ]
    }


async def test_search_cache_hit_skips_http_call(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = SearxngClient(base_url="http://localhost:8888", cache_ttl_sec=60.0)
    call_count = {"n": 0}

    async def fake_get_json(*_args: object, **_kwargs: object) -> dict[str, object]:
        call_count["n"] += 1
        return _fake_payload()

    monkeypatch.setattr(searxng_module, "get_json_with_retry", fake_get_json)

    r1 = await client.search("actualités tech")
    r2 = await client.search("actualités tech")
    assert r1 == r2
    assert call_count["n"] == 1
    await client.aclose()


async def test_search_cache_disabled(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = SearxngClient(base_url="http://localhost:8888", cache_ttl_sec=None)
    call_count = {"n": 0}

    async def fake_get_json(*_args: object, **_kwargs: object) -> dict[str, object]:
        call_count["n"] += 1
        return _fake_payload()

    monkeypatch.setattr(searxng_module, "get_json_with_retry", fake_get_json)

    await client.search("actualités tech")
    await client.search("actualités tech")
    assert call_count["n"] == 2
    await client.aclose()


async def test_search_cache_distinct_queries(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Deux requêtes différentes ne se court-circuitent pas."""
    client = SearxngClient(base_url="http://localhost:8888", cache_ttl_sec=60.0)
    call_count = {"n": 0}

    async def fake_get_json(*_args: object, **_kwargs: object) -> dict[str, object]:
        call_count["n"] += 1
        return _fake_payload()

    monkeypatch.setattr(searxng_module, "get_json_with_retry", fake_get_json)

    await client.search("a")
    await client.search("b")
    assert call_count["n"] == 2
    await client.aclose()


async def test_search_cache_returns_defensive_copy(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Muter le retour de search() ne doit pas corrompre le cache."""
    client = SearxngClient(base_url="http://localhost:8888", cache_ttl_sec=60.0)

    async def fake_get_json(*_args: object, **_kwargs: object) -> dict[str, object]:
        return _fake_payload()

    monkeypatch.setattr(searxng_module, "get_json_with_retry", fake_get_json)

    r1 = await client.search("q")
    r1.clear()
    r2 = await client.search("q")
    assert len(r2) == 2  # cache non affecté
    await client.aclose()


# Silencer l'unused-import "AsyncMock"
_ = AsyncMock
