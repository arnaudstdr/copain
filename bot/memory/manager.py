"""Mémoire sémantique : ChromaDB + embeddings Ollama nomic-embed-text."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from bot.logging_conf import get_logger

if TYPE_CHECKING:
    from bot.memory.embeddings import Embedder

log = get_logger(__name__)

COLLECTION_NAME = "personal_memory"

# Config HNSW pour la collection ChromaDB.
# - `cosine` convient pour `nomic-embed-text` (vecteurs non-normalisés).
# - `M=16` : valeur recommandée pour des corpus <100k embeddings.
# - `construction_ef=128` : build un peu plus long mais meilleur rappel.
# - `search_ef=64` : meilleur rappel que le défaut (10) pour un coût faible
#   sur des top_k petits (≤5 ici), compense la taille variable du corpus.
# Cette metadata n'est appliquée QU'À la création ; sur un chroma_dir
# existant, ChromaDB conserve la metadata de départ (on log un avertissement
# si divergence pour inviter à régénérer la collection manuellement).
HNSW_METADATA: dict[str, str | int] = {
    "hnsw:space": "cosine",
    "hnsw:M": 16,
    "hnsw:construction_ef": 128,
    "hnsw:search_ef": 64,
}


class MemoryManager:
    """Stocke et récupère des souvenirs factuels via embeddings vectoriels.

    Le `memory_content` (résumé factuel produit par le LLM) est ce qui est
    embedded. Le message brut est conservé en metadata pour debug uniquement.
    """

    def __init__(self, persist_dir: Path, embedder: Embedder) -> None:
        persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata=HNSW_METADATA,
        )
        self._embedder = embedder
        self._warn_on_hnsw_drift()

    def _warn_on_hnsw_drift(self) -> None:
        """Log un warning si une collection existante tourne avec une config HNSW différente."""
        actual = self._collection.metadata or {}
        drift = {k: (actual.get(k), v) for k, v in HNSW_METADATA.items() if actual.get(k) != v}
        if drift:
            log.warning(
                "chroma_hnsw_drift",
                drift=drift,
                hint=(
                    "La collection existante conserve sa metadata d'origine. "
                    "Pour appliquer la nouvelle config HNSW, supprime et régénère "
                    "data/chroma/ (la mémoire sera repartie de zéro)."
                ),
            )

    async def store(self, original_message: str, memory_content: str) -> None:
        """Embed le résumé factuel et le persiste dans ChromaDB."""
        vector = await self._embedder.embed(memory_content)
        entry_id = uuid.uuid4().hex
        metadata: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "original_message": original_message,
        }
        await asyncio.to_thread(
            self._collection.add,
            ids=[entry_id],
            embeddings=[vector],
            documents=[memory_content],
            metadatas=[metadata],
        )
        log.info("memory_stored", entry_id=entry_id, preview=memory_content[:80])

    async def store_many(self, items: Sequence[tuple[str, str]]) -> None:
        """Batch-embed et persiste plusieurs (original_message, memory_content).

        Les embeddings sont calculés en parallèle (`asyncio.gather`) puis
        une seule insertion ChromaDB est effectuée, ce qui évite N round-trips
        filesystem sur le volume Pi pour un import initial ou un digest
        hebdomadaire. Sans `items`, no-op.
        """
        if not items:
            return
        contents = [content for _, content in items]
        vectors = await self._embedder.embed_many(contents)
        now_iso = datetime.now(UTC).isoformat()
        ids = [uuid.uuid4().hex for _ in items]
        metadatas: list[dict[str, Any]] = [
            {"timestamp": now_iso, "original_message": original} for original, _ in items
        ]
        await asyncio.to_thread(
            self._collection.add,
            ids=ids,
            embeddings=vectors,
            documents=contents,
            metadatas=metadatas,
        )
        log.info("memory_stored_batch", count=len(items))

    async def retrieve_context(self, query: str, top_k: int = 5) -> list[str]:
        """Retourne les top_k documents les plus pertinents pour la requête."""
        vector = await self._embedder.embed(query)
        result = await asyncio.to_thread(
            self._collection.query,
            query_embeddings=[vector],
            n_results=top_k,
        )
        documents = result.get("documents") or [[]]
        return [doc for doc in documents[0] if isinstance(doc, str)]
