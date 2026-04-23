"""Client HTTP pour l'API Opendatasoft data.economie.gouv.fr (prix carburants)."""

from __future__ import annotations

import math
from datetime import datetime
from types import TracebackType
from typing import Any

import httpx

from bot.fuel.models import FuelStation, FuelType, GeoPoint
from bot.http_retry import get_json_with_retry
from bot.logging_conf import get_logger

log = get_logger(__name__)

BASE_URL = (
    "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/"
    "prix-des-carburants-en-france-flux-instantane-v2/records"
)

# Mapping FuelType → (colonne prix, colonne date de mise à jour) côté API ODS.
_FUEL_COLUMNS: dict[FuelType, tuple[str, str]] = {
    "gazole": ("gazole_prix", "gazole_maj"),
    "sp95": ("sp95_prix", "sp95_maj"),
    "sp98": ("sp98_prix", "sp98_maj"),
    "e10": ("e10_prix", "e10_maj"),
    "e85": ("e85_prix", "e85_maj"),
    "gplc": ("gplc_prix", "gplc_maj"),
}


class FuelError(RuntimeError):
    """Levée sur erreur HTTP ou réponse Opendatasoft non conforme."""


class FuelClient:
    """Wrapper httpx async pour interroger le dataset national des prix carburants."""

    def __init__(self, timeout: float = 20.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout)

    async def find_cheapest(
        self,
        fuel_type: FuelType,
        center: GeoPoint,
        radius_km: float,
        limit: int = 5,
    ) -> list[FuelStation]:
        """Retourne les N stations les moins chères pour `fuel_type` dans `radius_km`.

        Utilise le filtre géospatial natif `within_distance` de l'API ODS v2.1
        pour pousser le filtrage côté serveur. Distance retournée calculée
        côté client via Haversine (l'API ne la renvoie pas).
        """
        price_col, maj_col = _FUEL_COLUMNS[fuel_type]
        where = (
            f"within_distance(geom, geom'POINT({center.lon} {center.lat})', {radius_km}km)"
            f" and {price_col} is not null"
        )
        params: dict[str, Any] = {
            "where": where,
            "order_by": f"{price_col} asc",
            "limit": limit,
            "select": f"id,adresse,ville,cp,geom,{price_col},{maj_col}",
        }
        log.info(
            "fuel_request",
            fuel_type=fuel_type,
            lat=center.lat,
            lon=center.lon,
            radius_km=radius_km,
            limit=limit,
        )
        payload = await get_json_with_retry(
            self._client,
            BASE_URL,
            context="fuel:find_cheapest",
            error_cls=FuelError,
            params=params,
        )

        raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            raise FuelError("Champ 'results' absent ou invalide")

        stations: list[FuelStation] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            station = _parse_station(item, fuel_type, price_col, maj_col, center)
            if station is not None:
                stations.append(station)
        log.info("fuel_results_count", count=len(stations), fuel_type=fuel_type)
        return stations

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> FuelClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()


def _parse_station(
    item: dict[str, Any],
    fuel_type: FuelType,
    price_col: str,
    maj_col: str,
    center: GeoPoint,
) -> FuelStation | None:
    """Parse un record ODS en `FuelStation` ; renvoie None si payload inattendu."""
    price_raw = item.get(price_col)
    if price_raw is None:
        return None
    try:
        price = float(price_raw)
    except (TypeError, ValueError):
        return None

    coords = _extract_coords(item.get("geom"))
    if coords is None:
        return None
    lat, lon = coords

    return FuelStation(
        id=str(item.get("id", "")),
        address=str(item.get("adresse", "") or "").strip(),
        city=str(item.get("ville", "") or "").strip(),
        postal_code=str(item.get("cp", "") or "").strip(),
        lat=lat,
        lon=lon,
        distance_km=_haversine_km(center.lat, center.lon, lat, lon),
        fuel_type=fuel_type,
        price_eur=price,
        updated_at=_parse_iso(item.get(maj_col)),
    )


def _extract_coords(geom: Any) -> tuple[float, float] | None:
    """Extrait (lat, lon) d'un champ ODS `geom`.

    L'API v2.1 renvoie généralement un GeoJSON Point
    `{"type": "Point", "coordinates": [lon, lat]}` mais peut aussi renvoyer
    `{"lat": X, "lon": Y}` selon la version du dataset. On supporte les deux.
    """
    if not isinstance(geom, dict):
        return None
    coords = geom.get("coordinates")
    if isinstance(coords, list) and len(coords) == 2:
        try:
            lon = float(coords[0])
            lat = float(coords[1])
            return lat, lon
        except (TypeError, ValueError):
            return None
    lat_raw = geom.get("lat")
    lon_raw = geom.get("lon")
    if lat_raw is not None and lon_raw is not None:
        try:
            return float(lat_raw), float(lon_raw)
        except (TypeError, ValueError):
            return None
    return None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance orthodromique en km entre deux points (formule de Haversine)."""
    r = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c
