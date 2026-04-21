"""Mémoire sémantique : ChromaDB + embeddings Ollama nomic-embed-text."""

from __future__ import annotations

import asyncio
import uuid
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
        self._collection = self._client.get_or_create_collection(name=COLLECTION_NAME)
        self._embedder = embedder

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
            embeddings=[vector],  # type: ignore[arg-type]
            documents=[memory_content],
            metadatas=[metadata],
        )
        log.info("memory_stored", entry_id=entry_id, preview=memory_content[:80])

    async def retrieve_context(self, query: str, top_k: int = 5) -> list[str]:
        """Retourne les top_k documents les plus pertinents pour la requête."""
        vector = await self._embedder.embed(query)
        result = await asyncio.to_thread(
            self._collection.query,
            query_embeddings=[vector],  # type: ignore[arg-type]
            n_results=top_k,
        )
        documents = result.get("documents") or [[]]
        return [doc for doc in documents[0] if isinstance(doc, str)]
