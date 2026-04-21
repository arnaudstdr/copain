"""Client Ollama pour les embeddings `nomic-embed-text`."""

from __future__ import annotations

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

    def __init__(
        self, base_url: str, model: str, timeout: float = DEFAULT_TIMEOUT_SEC
    ) -> None:
        # Les embeddings sont rapides (<1 s en temps normal). 15 s suffisent
        # largement ; au-delà, Ollama est probablement indisponible.
        self._client = AsyncClient(host=base_url, timeout=httpx.Timeout(timeout))
        self._model = model

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
