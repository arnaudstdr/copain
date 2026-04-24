"""Client HTTP pour l'instance SearXNG locale."""

from __future__ import annotations

from types import TracebackType
from typing import TypedDict

import httpx

from bot.cache import TTLCache, hash_key
from bot.http_retry import get_json_with_retry
from bot.logging_conf import get_logger

log = get_logger(__name__)


class SearchResult(TypedDict):
    title: str
    url: str
    snippet: str


class SearxngError(RuntimeError):
    """Levée sur erreur HTTP ou réponse JSON non conforme."""


class SearxngClient:
    DEFAULT_CACHE_TTL_SEC = 3600.0
    DEFAULT_CACHE_MAX_SIZE = 128

    def __init__(
        self,
        base_url: str,
        timeout: float = 15.0,
        cache_ttl_sec: float | None = DEFAULT_CACHE_TTL_SEC,
        cache_max_size: int = DEFAULT_CACHE_MAX_SIZE,
    ) -> None:
        # cache_ttl_sec=None désactive le cache (utile en tests).
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)
        self._cache: TTLCache | None = (
            TTLCache(max_size=cache_max_size, ttl_sec=cache_ttl_sec)
            if cache_ttl_sec is not None
            else None
        )

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        cache_key = hash_key("searxng", query, limit) if self._cache else None
        if self._cache is not None and cache_key is not None:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                log.info("searxng_cache_hit", query=query, limit=limit)
                # copie défensive : les résultats ne doivent pas muter le cache
                return [dict(item) for item in cached]  # type: ignore[misc]

        url = f"{self._base_url}/search"
        params = {"q": query, "format": "json", "language": "fr"}
        payload = await get_json_with_retry(
            self._client,
            url,
            context="searxng:search",
            error_cls=SearxngError,
            params=params,
        )

        raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            raise SearxngError("Champ 'results' absent ou invalide")

        out: list[SearchResult] = []
        for item in raw_results[:limit]:
            if not isinstance(item, dict):
                continue
            out.append(
                SearchResult(
                    title=str(item.get("title", "")),
                    url=str(item.get("url", "")),
                    snippet=str(item.get("content", "")),
                )
            )

        if self._cache is not None and cache_key is not None:
            await self._cache.set(cache_key, [dict(item) for item in out])
        return out

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> SearxngClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
