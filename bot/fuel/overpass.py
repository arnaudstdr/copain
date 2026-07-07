"""Enrichissement des stations-service par leur enseigne via OpenStreetMap.

La donnée officielle `data.economie.gouv.fr` (cf. `bot/fuel/client.py`) ne
contient **aucune enseigne** (ni champ dédié, ni dans l'adresse). On la
récupère a posteriori auprès d'OpenStreetMap via l'API Overpass : une seule
requête ramène tous les points `amenity=fuel` de la zone, puis chaque station
officielle est appariée au point OSM le plus proche (`nearest_brand`).

L'enrichissement est **fail-soft** : toute erreur réseau/parsing laisse les
stations sans enseigne, jamais une exception qui casserait la réponse carburant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import TracebackType
from typing import Any

import httpx

from bot.fuel.models import GeoPoint
from bot.logging_conf import get_logger

log = get_logger(__name__)

# Endpoint Overpass public. Constante de module (comme `BASE_URL` de
# `NominatimClient`) plutôt que réglage env : pas de clé, valeur stable.
BASE_URL = "https://overpass-api.de/api/interpreter"

# Distance max (mètres) entre une station officielle et un point `amenity=fuel`
# OSM pour considérer l'appariement fiable. ~180 m : assez large pour absorber
# l'imprécision des coordonnées des deux sources, assez serré pour ne pas
# coller la mauvaise enseigne quand deux stations se côtoient.
_MATCH_MAX_METERS = 180.0


class OverpassError(RuntimeError):
    """Levée sur erreur HTTP ou payload Overpass inattendu."""


@dataclass(frozen=True, slots=True)
class BrandPoint:
    """Un point `amenity=fuel` OSM avec son enseigne résolue."""

    lat: float
    lon: float
    brand: str


class OverpassClient:
    """Wrapper httpx async autour de l'API Overpass (OpenStreetMap).

    La policy OSM impose un `User-Agent` identifiant l'appli (le même que pour
    Nominatim). L'usage mono-user reste très en dessous des limites publiques.
    """

    def __init__(self, user_agent: str, timeout: float = 15.0) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": user_agent},
        )

    async def find_fuel_stations(self, center: GeoPoint, radius_km: float) -> list[BrandPoint]:
        """Retourne les points `amenity=fuel` (avec enseigne) autour de `center`.

        Une seule requête Overpass couvre tout le rayon. Les nœuds sans enseigne
        exploitable (`brand`/`operator`/`name` absents) sont ignorés.
        """
        radius_m = int(radius_km * 1000)
        # `out;` (et non `out tags;`) : sur un nœud, ramène lat/lon ET tags —
        # `out tags;` omet les coordonnées, indispensables à l'appariement.
        query = (
            f"[out:json][timeout:25];"
            f"node[amenity=fuel](around:{radius_m},{center.lat},{center.lon});"
            f"out;"
        )
        try:
            response = await self._client.post(BASE_URL, data={"data": query})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("overpass_http_failed", error=str(exc))
            raise OverpassError(f"Appel Overpass échoué : {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise OverpassError("Réponse Overpass non-JSON") from exc

        elements = payload.get("elements") if isinstance(payload, dict) else None
        if not isinstance(elements, list):
            raise OverpassError("Champ 'elements' absent ou invalide")

        points: list[BrandPoint] = []
        for element in elements:
            point = _parse_element(element)
            if point is not None:
                points.append(point)
        log.info("overpass_results_count", count=len(points))
        return points

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> OverpassClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()


def _parse_element(element: Any) -> BrandPoint | None:
    """Parse un nœud Overpass en `BrandPoint` ; None si inexploitable."""
    if not isinstance(element, dict):
        return None
    lat_raw = element.get("lat")
    lon_raw = element.get("lon")
    if lat_raw is None or lon_raw is None:
        return None
    try:
        lat = float(lat_raw)
        lon = float(lon_raw)
    except (TypeError, ValueError):
        return None

    tags = element.get("tags")
    if not isinstance(tags, dict):
        return None
    brand = _resolve_brand(tags)
    if brand is None:
        return None
    return BrandPoint(lat=lat, lon=lon, brand=brand)


def _resolve_brand(tags: dict[str, Any]) -> str | None:
    """Choisit l'enseigne la plus fiable parmi les tags OSM d'une station.

    Ordre de préférence : `brand` (normalisé, ex. « TotalEnergies »), puis
    `operator`, puis `name` (souvent verbeux, ex. « Station Service E. Leclerc »).
    """
    for key in ("brand", "operator", "name"):
        value = tags.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def nearest_brand(
    lat: float,
    lon: float,
    points: list[BrandPoint],
    max_meters: float = _MATCH_MAX_METERS,
) -> str | None:
    """Enseigne du point OSM le plus proche de (lat, lon) sous `max_meters`.

    None si aucun point n'est assez proche (appariement jugé non fiable).
    Fonction pure : testable sans réseau.
    """
    best_brand: str | None = None
    best_dist = max_meters
    for point in points:
        dist = _haversine_m(lat, lon, point.lat, point.lon)
        if dist <= best_dist:
            best_dist = dist
            best_brand = point.brand
    return best_brand


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance orthodromique en mètres entre deux points (Haversine)."""
    r = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
