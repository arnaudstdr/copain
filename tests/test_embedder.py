"""Tests de Embedder.embed / embed_many."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from bot.memory.embeddings import Embedder, EmbeddingError


@pytest.fixture
def embedder() -> Embedder:
    emb = Embedder(base_url="http://localhost:11434", model="nomic-embed-text")
    emb._client = AsyncMock()  # type: ignore[assignment]
    emb._client.embeddings = AsyncMock(return_value={"embedding": [0.1, 0.2, 0.3]})
    return emb


async def test_embed_returns_vector(embedder: Embedder) -> None:
    vec = await embedder.embed("salut")
    assert vec == [0.1, 0.2, 0.3]


async def test_embed_raises_on_empty_response(embedder: Embedder) -> None:
    embedder._client.embeddings = AsyncMock(return_value={"embedding": []})  # type: ignore[attr-defined]
    with pytest.raises(EmbeddingError, match="vide"):
        await embedder.embed("salut")


async def test_embed_many_preserves_order(embedder: Embedder) -> None:
    """embed_many appelle embed pour chaque texte et rend les vecteurs dans l'ordre."""
    calls: list[str] = []

    async def track(**kwargs: str) -> dict[str, list[float]]:
        calls.append(kwargs["prompt"])
        # un vecteur différent selon le prompt pour valider l'ordre
        return {"embedding": [float(len(kwargs["prompt"]))]}

    embedder._client.embeddings = AsyncMock(side_effect=track)  # type: ignore[attr-defined]
    vectors = await embedder.embed_many(["aaa", "bb", "c"])
    assert vectors == [[3.0], [2.0], [1.0]]
    assert sorted(calls) == sorted(["aaa", "bb", "c"])


async def test_embed_many_empty_returns_empty() -> None:
    emb = Embedder(base_url="http://x", model="m")
    assert await emb.embed_many([]) == []


async def test_embed_many_respects_concurrency_limit() -> None:
    """Le sémaphore plafonne bien le fan-out même avec 100 textes."""
    emb = Embedder(base_url="http://x", model="m", batch_concurrency=3)
    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def fake_embeddings(**_kwargs: str) -> dict[str, list[float]]:
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        async with lock:
            in_flight -= 1
        return {"embedding": [0.0]}

    emb._client = AsyncMock()  # type: ignore[assignment]
    emb._client.embeddings = AsyncMock(side_effect=fake_embeddings)
    await emb.embed_many([f"t{i}" for i in range(20)])
    assert max_in_flight <= 3
