"""Wrapper async autour du client Ollama pour le LLM principal."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from ollama import AsyncClient

from bot.cache import TTLCache, hash_key
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


@dataclass
class _Endpoint:
    """Couple client Ollama + modèle + options (pour primary ET fallback)."""

    client: AsyncClient
    model: str
    timeout_sec: float
    num_ctx: int


class LLMClient:
    """Enveloppe minimale autour de `ollama.AsyncClient`.

    Les appels retournent le texte brut ; l'extraction du bloc <meta> est faite
    par `bot.llm.parser.extract_meta`. Les images (bytes) sont transmises en
    base64 via le champ `images` du message Ollama — supporté par les modèles
    multimodaux comme `gemma4:31b-cloud`, `llava`, `moondream`.
    """

    DEFAULT_TIMEOUT_SEC = 120.0
    DEFAULT_NUM_CTX = 32768
    DEFAULT_CACHE_TTL_SEC = 21600.0  # 6 h — résumés search/RSS restent pertinents
    DEFAULT_CACHE_MAX_SIZE = 128
    DEFAULT_FALLBACK_TIMEOUT_SEC = 60.0
    DEFAULT_FALLBACK_NUM_CTX = 8192

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        num_ctx: int = DEFAULT_NUM_CTX,
        cache_ttl_sec: float | None = DEFAULT_CACHE_TTL_SEC,
        cache_max_size: int = DEFAULT_CACHE_MAX_SIZE,
        fallback_model: str | None = None,
        fallback_base_url: str | None = None,
        fallback_timeout_sec: float = DEFAULT_FALLBACK_TIMEOUT_SEC,
        fallback_num_ctx: int = DEFAULT_FALLBACK_NUM_CTX,
    ) -> None:
        # Timeout explicite : Ollama cloud avec un 31B peut prendre 30-90 s sur
        # une recherche web (2 appels LLM chaînés). Défaut 120 s, configurable
        # via OLLAMA_TIMEOUT_SEC depuis bot.config.Settings.
        # num_ctx explicite : Ollama applique sinon un default serveur qui peut
        # retourner `prompt too long` même sur des prompts courts (bug cloud
        # constaté 04/2026). Passer la valeur explicitement court-circuite ça.
        # cache_ttl_sec=None désactive le cache (utile en tests unitaires).
        # fallback_model=None désactive le fallback.
        self._client = AsyncClient(host=base_url, timeout=httpx.Timeout(timeout))
        self._model = model
        self._timeout_sec = timeout
        self._num_ctx = num_ctx
        self._fallback: _Endpoint | None = None
        if fallback_model:
            # Même host par défaut : un Ollama local sur le Pi sert souvent à la
            # fois de proxy cloud et de host d'un petit modèle (gemma3:4b).
            self._fallback = _Endpoint(
                client=AsyncClient(
                    host=fallback_base_url or base_url,
                    timeout=httpx.Timeout(fallback_timeout_sec),
                ),
                model=fallback_model,
                timeout_sec=fallback_timeout_sec,
                num_ctx=fallback_num_ctx,
            )
        self._cache: TTLCache | None = (
            TTLCache(max_size=cache_max_size, ttl_sec=cache_ttl_sec)
            if cache_ttl_sec is not None
            else None
        )

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
        bloc <meta> (la décision de routing a déjà été prise). Le résultat est
        éligible au cache (`cacheable=True`) : pas de side effect possible.
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
            ],
            cacheable=True,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        cacheable: bool = False,
    ) -> str:
        """Appel bas niveau : envoie une liste de messages OpenAI-style, retourne le texte.

        `cacheable=True` active la mise en cache (clé = hash(model, messages)).
        À n'activer QUE pour des appels sans bloc <meta> et sans side effects :
        les appels principaux avec routing (qui déclenchent création de tâche,
        memory.store, etc.) doivent rester non cachés pour éviter des side
        effects dupliqués ou manqués.

        En cas d'échec du modèle principal (timeout ou erreur réseau) et si un
        `fallback_model` est configuré, un second essai est fait sur le modèle
        de secours local. Les réponses du fallback ne sont PAS mises en cache
        (qualité moindre — on préfère retenter le primary au prochain appel).
        """
        cache_key: str | None = None
        if cacheable and self._cache is not None:
            cache_key = hash_key("llm", self._model, messages)
            cached = await self._cache.get(cache_key)
            if cached is not None:
                log.info("llm_cache_hit", model=self._model)
                return str(cached)

        used_fallback = False
        try:
            content = await self._chat_on(
                self._client,
                model=self._model,
                timeout_sec=self._timeout_sec,
                num_ctx=self._num_ctx,
                messages=messages,
            )
        except (LLMTimeoutError, LLMError) as primary_exc:
            # Pas de fallback possible si la requête embarque des images :
            # le modèle local par défaut n'est pas multimodal.
            has_images = any("images" in msg for msg in messages)
            if self._fallback is None or has_images:
                raise
            log.warning(
                "llm_fallback_triggered",
                primary_model=self._model,
                fallback_model=self._fallback.model,
                reason=type(primary_exc).__name__,
            )
            try:
                content = await self._chat_on(
                    self._fallback.client,
                    model=self._fallback.model,
                    timeout_sec=self._fallback.timeout_sec,
                    num_ctx=self._fallback.num_ctx,
                    messages=messages,
                )
            except (LLMTimeoutError, LLMError):
                log.error(
                    "llm_fallback_failed",
                    primary_model=self._model,
                    fallback_model=self._fallback.model,
                )
                # Ré-émet l'erreur d'origine (primary) : UX + cohérence des
                # logs d'erreurs côté handler.
                raise primary_exc from primary_exc.__cause__
            used_fallback = True

        # Les réponses issues du fallback ne sont pas cachées : on veut
        # retenter le primary au prochain appel (cloud a pu repasser OK).
        if cache_key is not None and self._cache is not None and not used_fallback:
            await self._cache.set(cache_key, content)
        return content

    def _dump_failed_prompt(self, messages: list[dict[str, Any]], exc: BaseException) -> None:
        """Persiste le prompt dans /tmp/copain_failed_prompt.json pour diagnostic.

        Seulement sur erreurs inattendues (typiquement `prompt too long`).
        Écrase à chaque fois — on ne garde que le dernier.
        """
        if "prompt too long" not in str(exc).lower():
            return
        try:
            import json
            from pathlib import Path

            Path("/tmp/copain_failed_prompt.json").write_text(
                json.dumps({"error": str(exc), "messages": messages}, ensure_ascii=False, indent=2)
            )
            log.warning("failed_prompt_dumped", path="/tmp/copain_failed_prompt.json")
        except Exception as dump_exc:
            log.warning("failed_prompt_dump_error", error=str(dump_exc))

    def _log_prompt_size(self, model: str, messages: list[dict[str, Any]]) -> None:
        """Log la taille approx du prompt (chars + estim. tokens ~chars/4) par rôle.

        Sert à diagnostiquer les prompts anormalement gros (mémoire non tronquée,
        history accumulé, résultats de recherche injectés tels quels) qui
        déclenchent un `prompt too long` côté Ollama.
        """
        by_role: dict[str, int] = {}
        for msg in messages:
            role = str(msg.get("role", "?"))
            content = msg.get("content") or ""
            by_role[role] = by_role.get(role, 0) + len(content)
            for img_b64 in msg.get("images") or []:
                by_role[role] += len(img_b64)
        total_chars = sum(by_role.values())
        log.info(
            "llm_prompt_size",
            model=model,
            messages=len(messages),
            total_chars=total_chars,
            est_tokens=total_chars // 4,
            by_role=by_role,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        cacheable: bool = False,
    ) -> AsyncIterator[str]:
        """Version streamée de `chat` : yield des chunks de texte au fil de l'eau.

        Sur cache hit, un seul chunk est yieldé (contenu complet). En cas
        d'erreur avant le premier chunk produit par Ollama et si un fallback
        est configuré, on bascule sur le fallback en mode non-streamé (un seul
        yield). En cas d'erreur APRÈS qu'au moins un chunk ait été produit,
        l'exception est ré-émise (impossible d'« annuler » ce qu'on a déjà
        affiché à l'utilisateur). Ne fallback pas si la requête contient des
        images.
        """
        cache_key: str | None = None
        if cacheable and self._cache is not None:
            cache_key = hash_key("llm", self._model, messages)
            cached = await self._cache.get(cache_key)
            if cached is not None:
                log.info("llm_cache_hit", model=self._model)
                yield str(cached)
                return

        buffer_parts: list[str] = []
        produced_any = False
        self._log_prompt_size(self._model, messages)
        try:
            stream = await self._client.chat(
                model=self._model,
                messages=messages,
                options={"num_ctx": self._num_ctx},
                stream=True,
            )
            async for chunk in stream:
                piece = chunk.get("message", {}).get("content") or ""
                if piece:
                    produced_any = True
                    buffer_parts.append(piece)
                    yield piece
        except httpx.TimeoutException as exc:
            log.warning(
                "ollama_chat_timeout",
                model=self._model,
                timeout_sec=self._timeout_sec,
                during_stream=produced_any,
            )
            if produced_any:
                raise LLMTimeoutError(
                    f"Ollama n'a pas répondu dans les {self._timeout_sec:.0f} s impartis"
                ) from exc
            has_images = any("images" in msg for msg in messages)
            if self._fallback is None or has_images:
                raise LLMTimeoutError(
                    f"Ollama n'a pas répondu dans les {self._timeout_sec:.0f} s impartis"
                ) from exc
            fallback_content = await self._fallback_chat(messages, LLMTimeoutError)
            buffer_parts = [fallback_content]
            yield fallback_content
        except Exception as exc:
            log.error("ollama_chat_failed", model=self._model, error=str(exc))
            self._dump_failed_prompt(messages, exc)
            if produced_any:
                raise LLMError(f"Appel Ollama échoué : {exc}") from exc
            has_images = any("images" in msg for msg in messages)
            if self._fallback is None or has_images:
                raise LLMError(f"Appel Ollama échoué : {exc}") from exc
            fallback_content = await self._fallback_chat(messages, LLMError)
            buffer_parts = [fallback_content]
            yield fallback_content

        full = "".join(buffer_parts)
        # On ne cache que si la réponse ne vient pas du fallback. Ici, dans le
        # flow nominal (pas d'exception), full est bien du primary.
        if cache_key is not None and self._cache is not None and full:
            await self._cache.set(cache_key, full)

    async def _fallback_chat(
        self,
        messages: list[dict[str, Any]],
        primary_exc_cls: type[LLMError],
    ) -> str:
        """Appelle le fallback (non-streamé). Utilisé par chat_stream en cas d'échec primary."""
        assert self._fallback is not None
        log.warning(
            "llm_fallback_triggered",
            primary_model=self._model,
            fallback_model=self._fallback.model,
            reason="stream_pre_chunk",
        )
        try:
            return await self._chat_on(
                self._fallback.client,
                model=self._fallback.model,
                timeout_sec=self._fallback.timeout_sec,
                num_ctx=self._fallback.num_ctx,
                messages=messages,
            )
        except (LLMTimeoutError, LLMError) as exc:
            log.error(
                "llm_fallback_failed",
                primary_model=self._model,
                fallback_model=self._fallback.model,
            )
            raise primary_exc_cls(f"Primary et fallback ont échoué ({exc})") from exc

    async def _chat_on(
        self,
        client: AsyncClient,
        *,
        model: str,
        timeout_sec: float,
        num_ctx: int,
        messages: list[dict[str, Any]],
    ) -> str:
        """Appel Ollama unitaire (un endpoint), utilisé par primary et fallback."""
        self._log_prompt_size(model, messages)
        try:
            response: Any = await client.chat(
                model=model,
                messages=messages,
                options={"num_ctx": num_ctx},
            )
        except httpx.TimeoutException as exc:
            log.warning("ollama_chat_timeout", model=model, timeout_sec=timeout_sec)
            raise LLMTimeoutError(
                f"Ollama n'a pas répondu dans les {timeout_sec:.0f} s impartis"
            ) from exc
        except Exception as exc:
            log.error("ollama_chat_failed", model=model, error=str(exc))
            raise LLMError(f"Appel Ollama échoué : {exc}") from exc

        content = response.get("message", {}).get("content")
        if not isinstance(content, str) or not content:
            raise LLMError("Réponse Ollama sans contenu texte")
        return content
