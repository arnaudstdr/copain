"""Tests du MemoryManager contre une collection ChromaDB persistée en tmp."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from bot.memory.manager import HNSW_METADATA, DepotMatch, MemoryManager


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


# --- find_similar_depots -----------------------------------------------------


@pytest.fixture
def depot_embedder() -> AsyncMock:
    """Embedder déterministe : un vecteur fixe par texte connu.

    L'espace est cosine ; les distances attendues face à la requête
    `[1, 0, 0, 0]` sont donc maîtrisées :
    - `[1.0, 0.0, …]`  → 0.0
    - `[0.95, 0.05, …]` → ≈ 0.0014
    - `[0.9, 0.1, …]`   → ≈ 0.0062
    - `[0.85, 0.15, …]` → ≈ 0.0152
    - `[0.0, 1.0, …]`   → 1.0
    """
    vectors: dict[str, list[float]] = {
        "j'ai peur pour l'entretien": [1.0, 0.0, 0.0, 0.0],
        "encore ce stress d'entretien": [0.95, 0.05, 0.0, 0.0],
        "l'entretien m'angoisse": [0.9, 0.1, 0.0, 0.0],
        "l'entretien me stresse un peu": [0.85, 0.15, 0.0, 0.0],
        "idée : une appli de recettes": [0.0, 1.0, 0.0, 0.0],
        "rdv dentiste mardi": [1.0, 0.0, 0.0, 0.0],
        "je repense à l'entretien": [1.0, 0.0, 0.0, 0.0],
    }
    embedder = AsyncMock()

    async def fake_embed(text: str) -> list[float]:
        return vectors[text]

    embedder.embed.side_effect = fake_embed
    return embedder


async def test_find_similar_depots_filters_kind_and_sorts(
    tmp_data_dir: Path, depot_embedder: AsyncMock
) -> None:
    """Seuls les dépôts remontent (where kind=depot), triés par distance croissante."""
    manager = MemoryManager(tmp_data_dir / "chroma", depot_embedder)
    await manager.store_depot("j'ai peur pour l'entretien", thought_id=1, thought_kind="worry")
    await manager.store_depot("l'entretien m'angoisse", thought_id=2, thought_kind="worry")
    await manager.store_depot("idée : une appli de recettes", thought_id=3, thought_kind="idea")
    # Souvenir générique avec le MÊME vecteur que la requête : sans le filtre
    # `where`, il serait en tête des résultats.
    await manager.store(original_message="brut", memory_content="rdv dentiste mardi")

    matches = await manager.find_similar_depots("je repense à l'entretien", max_distance=0.35)

    assert [m.thought_id for m in matches] == [1, 2]
    assert all(isinstance(m, DepotMatch) for m in matches)
    assert matches[0].content == "j'ai peur pour l'entretien"
    assert matches[1].content == "l'entretien m'angoisse"
    assert matches[0].distance <= matches[1].distance <= 0.35


async def test_find_similar_depots_tight_max_distance(
    tmp_data_dir: Path, depot_embedder: AsyncMock
) -> None:
    """Un seuil serré exclut les voisins trop éloignés."""
    manager = MemoryManager(tmp_data_dir / "chroma", depot_embedder)
    await manager.store_depot("j'ai peur pour l'entretien", thought_id=1, thought_kind="worry")
    await manager.store_depot("l'entretien m'angoisse", thought_id=2, thought_kind="worry")

    matches = await manager.find_similar_depots("je repense à l'entretien", max_distance=0.003)

    assert [m.thought_id for m in matches] == [1]


async def test_find_similar_depots_empty_collection(
    tmp_data_dir: Path, depot_embedder: AsyncMock
) -> None:
    manager = MemoryManager(tmp_data_dir / "chroma", depot_embedder)
    matches = await manager.find_similar_depots("je repense à l'entretien", max_distance=0.35)
    assert matches == []


async def test_find_similar_depots_ignores_invalid_thought_id(
    tmp_data_dir: Path, depot_embedder: AsyncMock
) -> None:
    """Un match dont la metadata `thought_id` est absente ou invalide est ignoré."""
    manager = MemoryManager(tmp_data_dir / "chroma", depot_embedder)
    await manager.store_depot("l'entretien m'angoisse", thought_id=2, thought_kind="worry")
    # Matchs orphelins insérés à la main : sans thought_id, puis non-entier.
    manager._collection.add(
        ids=["orphan-missing", "orphan-str"],
        embeddings=[[1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]],  # type: ignore[arg-type, unused-ignore]
        documents=["dépôt sans thought_id", "dépôt thought_id invalide"],
        metadatas=[{"kind": "depot"}, {"kind": "depot", "thought_id": "abc"}],
    )

    matches = await manager.find_similar_depots("je repense à l'entretien", max_distance=0.35)

    assert [m.thought_id for m in matches] == [2]


async def test_find_similar_depots_respects_top_k(
    tmp_data_dir: Path, depot_embedder: AsyncMock
) -> None:
    manager = MemoryManager(tmp_data_dir / "chroma", depot_embedder)
    await manager.store_depot("j'ai peur pour l'entretien", thought_id=1, thought_kind="worry")
    await manager.store_depot("encore ce stress d'entretien", thought_id=2, thought_kind="worry")
    await manager.store_depot("l'entretien m'angoisse", thought_id=3, thought_kind="worry")
    await manager.store_depot("l'entretien me stresse un peu", thought_id=4, thought_kind="worry")

    matches = await manager.find_similar_depots(
        "je repense à l'entretien", top_k=2, max_distance=0.35
    )

    # Les deux plus proches uniquement (0.0 puis ≈0.0014)
    assert [m.thought_id for m in matches] == [1, 2]
