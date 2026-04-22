"""Client de géocoding Nominatim (OpenStreetMap) limité à la France."""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx

from bot.fuel.models import GeoPoint
from bot.logging_conf import get_logger

log = get_logger(__name__)

BASE_URL = "https://nominatim.openstreetmap.org/search"

# Cache mémoire borné : le bot est mono-user, quelques dizaines d'entrées
# suffisent largement. Un simple dict avec éviction FIFO quand plein.
_CACHE_MAX_SIZE = 128


class NominatimError(RuntimeError):
    """Levée sur erreur HTTP ou payload Nominatim inattendu."""


class NominatimClient:
    """Wrapper httpx async autour de l'API Nominatim.

    La policy OSM impose un `User-Agent` identifiant explicitement l'appli
    (cf. https://operations.osmfoundation.org/policies/nominatim/) et un
    rate-limit de 1 requête/seconde max. Pour un usage mono-user + cache,
    on est largement en dessous de cette limite.
    """

    def __init__(self, user_agent: str, timeout: float = 10.0) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": user_agent},
        )
        self._cache: dict[str, GeoPoint] = {}

    async def geocode_fr(self, query: str) -> GeoPoint | None:
        """Géocode une chaîne FR (ville, adresse) en GeoPoint. `None` si inconnu."""
        key = query.strip().lower()
        if not key:
            return None
        cached = self._cache.get(key)
        if cached is not None:
            log.info("geocode_hit", query=key)
            return cached

        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "limit": 1,
            "countrycodes": "fr",
        }
        try:
            response = await self._client.get(BASE_URL, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("geocode_http_failed", query=key, error=str(exc))
            raise NominatimError(f"Appel Nominatim échoué : {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise NominatimError("Réponse Nominatim non-JSON") from exc

        if not isinstance(payload, list) or not payload:
            log.info("geocode_miss", query=key)
            return None

        first = payload[0]
        if not isinstance(first, dict):
            return None
        try:
            point = GeoPoint(lat=float(first["lat"]), lon=float(first["lon"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise NominatimError(f"Payload Nominatim inattendu : {exc}") from exc

        self._cache_put(key, point)
        log.info("geocode_miss_resolved", query=key, lat=point.lat, lon=point.lon)
        return point

    def _cache_put(self, key: str, value: GeoPoint) -> None:
        if len(self._cache) >= _CACHE_MAX_SIZE:
            # Éviction FIFO simple : on retire la première clé insérée.
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = value

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> NominatimClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
