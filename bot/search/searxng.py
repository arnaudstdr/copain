"""Client HTTP pour l'instance SearXNG locale."""

from __future__ import annotations

from types import TracebackType
from typing import TypedDict

import httpx

from bot.logging_conf import get_logger

log = get_logger(__name__)


class SearchResult(TypedDict):
    title: str
    url: str
    snippet: str


class SearxngError(RuntimeError):
    """Levée sur erreur HTTP ou réponse JSON non conforme."""


class SearxngClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        url = f"{self._base_url}/search"
        params = {"q": query, "format": "json", "language": "fr"}
        try:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("searxng_http_failed", url=url, error=str(exc))
            raise SearxngError(f"Appel SearXNG échoué : {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise SearxngError("Réponse SearXNG non-JSON") from exc

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
