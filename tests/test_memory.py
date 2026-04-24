"""Tests du MemoryManager contre une collection ChromaDB persistée en tmp."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from bot.memory.manager import HNSW_METADATA, MemoryManager


@pytest.fixture
def embedder_with_varying_vectors() -> AsyncMock:
    """Embedder qui renvoie un vecteur différent à chaque appel (pour diversité)."""
    embedder = AsyncMock()
    counter = {"n": 0}

    async def fake_embed(text: str) -> list[float]:
        counter["n"] += 1
        # Huit dimensions, valeur dépend du texte pour permettre la similarité
        base = float(len(text) % 10) / 10
        return [base + i * 0.01 for i in range(8)]

    async def fake_embed_many(texts: list[str]) -> list[list[float]]:
        return [await fake_embed(t) for t in texts]

    embedder.embed.side_effect = fake_embed
    embedder.embed_many.side_effect = fake_embed_many
    return embedder


async def test_store_then_retrieve_returns_document(
    tmp_data_dir: Path, embedder_with_varying_vectors: AsyncMock
) -> None:
    manager = MemoryManager(tmp_data_dir / "chroma", embedder_with_varying_vectors)

    await manager.store(
        original_message="J'ai rendez-vous chez le dentiste mardi prochain",
        memory_content="Rendez-vous dentiste mardi prochain",
    )

    results = await manager.retrieve_context("quand est mon rdv dentiste ?", top_k=5)
    assert any("dentiste" in doc.lower() for doc in results)


async def test_retrieve_empty_collection(
    tmp_data_dir: Path, embedder_with_varying_vectors: AsyncMock
) -> None:
    manager = MemoryManager(tmp_data_dir / "chroma", embedder_with_varying_vectors)
    results = await manager.retrieve_context("n'importe quoi", top_k=5)
    assert results == []


async def test_collection_created_with_hnsw_cosine_metadata(
    tmp_data_dir: Path, embedder_with_varying_vectors: AsyncMock
) -> None:
    """Une nouvelle collection doit porter la metadata HNSW cosine."""
    manager = MemoryManager(tmp_data_dir / "chroma", embedder_with_varying_vectors)
    meta = manager._collection.metadata or {}
    for key, expected in HNSW_METADATA.items():
        assert meta.get(key) == expected


async def test_store_many_batches_embeddings_and_inserts(
    tmp_data_dir: Path, embedder_with_varying_vectors: AsyncMock
) -> None:
    manager = MemoryManager(tmp_data_dir / "chroma", embedder_with_varying_vectors)
    items = [
        ("brut 1", "résumé 1"),
        ("brut 2", "résumé 2"),
        ("brut 3", "résumé 3"),
    ]
    await manager.store_many(items)

    # embed_many appelé une seule fois avec les trois contenus
    assert embedder_with_varying_vectors.embed_many.call_count == 1
    called_texts = embedder_with_varying_vectors.embed_many.call_args.args[0]
    assert called_texts == ["résumé 1", "résumé 2", "résumé 3"]

    # les 3 docs sont effectivement dans la collection
    all_docs = manager._collection.get()
    assert len(all_docs["ids"]) == 3


async def test_store_many_no_op_on_empty(
    tmp_data_dir: Path, embedder_with_varying_vectors: AsyncMock
) -> None:
    manager = MemoryManager(tmp_data_dir / "chroma", embedder_with_varying_vectors)
    await manager.store_many([])
    assert embedder_with_varying_vectors.embed_many.call_count == 0
