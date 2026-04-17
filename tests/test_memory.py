"""Tests du MemoryManager contre une collection ChromaDB persistée en tmp."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from bot.memory.manager import MemoryManager


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

    embedder.embed.side_effect = fake_embed
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
