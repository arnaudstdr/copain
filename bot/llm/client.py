"""Wrapper async autour du client Ollama pour le LLM principal."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ollama import AsyncClient

from bot.logging_conf import get_logger

log = get_logger(__name__)


class LLMError(RuntimeError):
    """Erreur remontée par le client Ollama (réseau, timeout, modèle absent)."""


class LLMClient:
    """Enveloppe minimale autour de `ollama.AsyncClient`.

    Les appels retournent le texte brut ; l'extraction du bloc <meta> est faite
    par `bot.llm.parser.extract_meta`.
    """

    def __init__(self, base_url: str, model: str) -> None:
        self._client = AsyncClient(host=base_url)
        self._model = model

    async def call(self, system: str, user: str) -> str:
        """Appelle le LLM avec un system prompt + un message utilisateur."""
        return await self._chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )

    async def call_with_search(
        self,
        original_message: str,
        results: Iterable[dict[str, str]],
    ) -> str:
        """Relance le LLM en injectant les résultats SearXNG dans le contexte.

        Utilise un system prompt dédié qui demande un résumé en français sans
        bloc <meta> (la décision de routing a déjà été prise).
        """
        formatted = "\n".join(
            f"- {r.get('title', '?')} ({r.get('url', '')}) : {r.get('snippet', '')}"
            for r in results
        )
        system = (
            "Tu es l'assistant personnel d'Arnaud. Voici des résultats de recherche web. "
            "Réponds en français, concis, en citant les sources pertinentes (URL). "
            "N'inclus PAS de bloc <meta>."
        )
        user = f"Question initiale : {original_message}\n\nRésultats :\n{formatted}"
        return await self._chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )

    async def _chat(self, messages: list[dict[str, str]]) -> str:
        try:
            response: Any = await self._client.chat(model=self._model, messages=messages)
        except Exception as exc:
            log.error("ollama_chat_failed", model=self._model, error=str(exc))
            raise LLMError(f"Appel Ollama échoué : {exc}") from exc

        content = response.get("message", {}).get("content")
        if not isinstance(content, str) or not content:
            raise LLMError("Réponse Ollama sans contenu texte")
        return content
