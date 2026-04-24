"""Cache TTL + LRU générique pour réponses LLM et SearXNG.

Utilisé par `LLMClient.chat` (opt-in via `cacheable=True`) et par
`SearxngClient.search`. Les deux sont indépendants : chaque client porte
son propre `TTLCache` avec son TTL et sa taille max.

Principes :
- Clés : `str` (les callers calculent un sha256 pour les payloads complexes).
- Valeurs : `Any` (typiquement `str` pour le LLM, `list[dict]` pour SearXNG).
- Expiration paresseuse : les entrées expirées sont purgées uniquement lors
  d'un `get` ou lorsque `max_size` est dépassé sur `set`.
- Async-safe via `asyncio.Lock` : sans ça, deux coroutines peuvent stocker
  la même clé en concurrence et gonfler inutilement le cache.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from typing import Any


def hash_key(*parts: Any) -> str:
    """Calcule un sha256 stable à partir d'éléments hétérogènes (JSON-sérialisés)."""
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TTLCache:
    """Cache LRU avec expiration TTL, async-safe."""

    def __init__(self, max_size: int, ttl_sec: float) -> None:
        if max_size <= 0:
            raise ValueError("max_size doit être > 0")
        if ttl_sec <= 0:
            raise ValueError("ttl_sec doit être > 0")
        self._max_size = max_size
        self._ttl_sec = ttl_sec
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0

    async def get(self, key: str) -> Any | None:
        """Retourne la valeur associée ou `None` si absente/expirée."""
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            expires_at, value = entry
            if expires_at < time.monotonic():
                # expiration paresseuse
                self._store.pop(key, None)
                self._misses += 1
                return None
            # move-to-end pour maintenir l'ordre LRU
            self._store.move_to_end(key)
            self._hits += 1
            return value

    async def set(self, key: str, value: Any) -> None:
        """Insère ou remplace une entrée et applique la limite de taille."""
        async with self._lock:
            expires_at = time.monotonic() + self._ttl_sec
            self._store[key] = (expires_at, value)
            self._store.move_to_end(key)
            while len(self._store) > self._max_size:
                self._store.popitem(last=False)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()

    def stats(self) -> dict[str, int]:
        return {"size": len(self._store), "hits": self._hits, "misses": self._misses}
