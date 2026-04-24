"""Client Ollama pour les embeddings `nomic-embed-text`."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import httpx
from ollama import AsyncClient

from bot.logging_conf import get_logger

log = get_logger(__name__)


class EmbeddingError(RuntimeError):
    """Levée si le modèle d'embeddings renvoie une réponse invalide."""


class Embedder:
    """Petit wrapper async autour de `ollama.AsyncClient.embeddings`."""

    DEFAULT_TIMEOUT_SEC = 15.0
    DEFAULT_BATCH_CONCURRENCY = 4

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        batch_concurrency: int = DEFAULT_BATCH_CONCURRENCY,
    ) -> None:
        # Les embeddings sont rapides (<1 s en temps normal). 15 s suffisent
        # largement ; au-delà, Ollama est probablement indisponible.
        # batch_concurrency plafonne le fan-out d'embed_many : Ollama local
        # sur le Pi 5 sature vite au-delà de 4 requêtes simultanées.
        self._client = AsyncClient(host=base_url, timeout=httpx.Timeout(timeout))
        self._model = model
        self._batch_semaphore = asyncio.Semaphore(batch_concurrency)

    async def embed(self, text: str) -> list[float]:
        try:
            response: Any = await self._client.embeddings(model=self._model, prompt=text)
        except Exception as exc:
            log.error("embedding_failed", model=self._model, error=str(exc))
            raise EmbeddingError(f"Embedding Ollama échoué : {exc}") from exc

        vector = response.get("embedding")
        if not isinstance(vector, list) or not vector:
            raise EmbeddingError("Réponse d'embedding vide ou malformée")
        return [float(x) for x in vector]

    async def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed plusieurs textes en parallèle (borné par `batch_concurrency`).

        Ollama n'expose pas d'endpoint batch natif pour `embeddings` ; on
        parallèlise côté client avec un sémaphore pour limiter le fan-out.
        Utile pour un import initial ou un digest périodique qui pousse N
        entrées d'un coup dans ChromaDB.
        """
        if not texts:
            return []

        async def _one(text: str) -> list[float]:
            async with self._batch_semaphore:
                return await self.embed(text)

        return await asyncio.gather(*(_one(t) for t in texts))
