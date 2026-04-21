"""Wrapper async autour du client Ollama pour le LLM principal."""

from __future__ import annotations

import base64
from collections.abc import Iterable, Mapping
from typing import Any

import httpx
from ollama import AsyncClient

from bot.logging_conf import get_logger

log = get_logger(__name__)


class LLMError(RuntimeError):
    """Erreur remontée par le client Ollama (réseau, modèle absent, etc.)."""


class LLMTimeoutError(LLMError):
    """Levée spécifiquement quand Ollama dépasse le timeout configuré.

    Sous-classe de LLMError pour que tout code qui catche LLMError la récupère
    aussi. Permet aux handlers de donner un message utilisateur dédié
    « le modèle met trop longtemps à répondre ».
    """


class LLMClient:
    """Enveloppe minimale autour de `ollama.AsyncClient`.

    Les appels retournent le texte brut ; l'extraction du bloc <meta> est faite
    par `bot.llm.parser.extract_meta`. Les images (bytes) sont transmises en
    base64 via le champ `images` du message Ollama — supporté par les modèles
    multimodaux comme `gemma4:31b-cloud`, `llava`, `moondream`.
    """

    DEFAULT_TIMEOUT_SEC = 120.0

    def __init__(self, base_url: str, model: str, timeout: float = DEFAULT_TIMEOUT_SEC) -> None:
        # Timeout explicite : Ollama cloud avec un 31B peut prendre 30-90 s sur
        # une recherche web (2 appels LLM chaînés). Défaut 120 s, configurable
        # via OLLAMA_TIMEOUT_SEC depuis bot.config.Settings.
        self._client = AsyncClient(host=base_url, timeout=httpx.Timeout(timeout))
        self._model = model
        self._timeout_sec = timeout

    async def call(
        self,
        system: str,
        user: str,
        images: list[bytes] | None = None,
    ) -> str:
        """Appelle le LLM avec un system prompt + un message utilisateur (+ images optionnelles)."""
        user_msg: dict[str, Any] = {"role": "user", "content": user}
        if images:
            user_msg["images"] = [base64.b64encode(img).decode("ascii") for img in images]
        return await self.chat(
            messages=[
                {"role": "system", "content": system},
                user_msg,
            ]
        )

    async def call_with_search(
        self,
        original_message: str,
        results: Iterable[Mapping[str, Any]],
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
        return await self.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        """Appel bas niveau : envoie une liste de messages OpenAI-style, retourne le texte."""
        try:
            response: Any = await self._client.chat(model=self._model, messages=messages)
        except httpx.TimeoutException as exc:
            log.warning("ollama_chat_timeout", model=self._model, timeout_sec=self._timeout_sec)
            raise LLMTimeoutError(
                f"Ollama n'a pas répondu dans les {self._timeout_sec:.0f} s impartis"
            ) from exc
        except Exception as exc:
            log.error("ollama_chat_failed", model=self._model, error=str(exc))
            raise LLMError(f"Appel Ollama échoué : {exc}") from exc

        content = response.get("message", {}).get("content")
        if not isinstance(content, str) or not content:
            raise LLMError("Réponse Ollama sans contenu texte")
        return content
