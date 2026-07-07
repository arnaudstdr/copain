"""Tests de l'enrichissement enseigne via Overpass (OpenStreetMap)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from bot.fuel.models import GeoPoint
from bot.fuel.overpass import (
    BrandPoint,
    OverpassClient,
    OverpassError,
    nearest_brand,
)


def _payload(elements: list[dict]) -> dict:
    return {"version": 0.6, "elements": elements}


def _node(lat: float, lon: float, **tags: str) -> dict:
    return {"type": "node", "id": 1, "lat": lat, "lon": lon, "tags": tags}


async def test_find_fuel_stations_parses_nodes_and_builds_query() -> None:
    response = MagicMock()
    response.json.return_value = _payload(
        [
            _node(48.26, 7.45, brand="TotalEnergies"),
            _node(48.27, 7.46, operator="Beysang"),
            _node(48.28, 7.47, name="Station E. Leclerc"),
        ]
    )
    response.raise_for_status = MagicMock()

    client = OverpassClient(user_agent="test")
    client._client = AsyncMock()
    client._client.post = AsyncMock(return_value=response)

    points = await client.find_fuel_stations(GeoPoint(lat=48.26, lon=7.45), radius_km=10.0)

    assert [p.brand for p in points] == ["TotalEnergies", "Beysang", "Station E. Leclerc"]
    # Requête Overpass : rayon converti en mètres, centrée sur le point.
    data = client._client.post.call_args.kwargs["data"]["data"]
    assert "around:10000,48.26,7.45" in data
    assert "node[amenity=fuel]" in data


async def test_find_fuel_stations_skips_nodes_without_brand_or_coords() -> None:
    response = MagicMock()
    response.json.return_value = _payload(
        [
            _node(48.26, 7.45),  # aucun tag exploitable
            {"type": "node", "tags": {"brand": "Total"}},  # pas de coords
            _node(48.30, 7.50, brand="Intermarché"),
        ]
    )
    response.raise_for_status = MagicMock()

    client = OverpassClient(user_agent="test")
    client._client = AsyncMock()
    client._client.post = AsyncMock(return_value=response)

    points = await client.find_fuel_stations(GeoPoint(lat=48.26, lon=7.45), radius_km=5.0)
    assert [p.brand for p in points] == ["Intermarché"]


async def test_find_fuel_stations_http_error_raises_overpass_error() -> None:
    client = OverpassClient(user_agent="test")
    client._client = AsyncMock()
    client._client.post = AsyncMock(side_effect=httpx.ConnectTimeout("boom"))

    with pytest.raises(OverpassError):
        await client.find_fuel_stations(GeoPoint(lat=48.0, lon=7.0), radius_km=10.0)


async def test_find_fuel_stations_invalid_payload_raises() -> None:
    response = MagicMock()
    response.json.return_value = {"no_elements": True}
    response.raise_for_status = MagicMock()

    client = OverpassClient(user_agent="test")
    client._client = AsyncMock()
    client._client.post = AsyncMock(return_value=response)

    with pytest.raises(OverpassError):
        await client.find_fuel_stations(GeoPoint(lat=48.0, lon=7.0), radius_km=10.0)


def test_nearest_brand_matches_closest_within_threshold() -> None:
    points = [
        BrandPoint(lat=48.2601, lon=7.4501, brand="Total"),  # ~14 m
        BrandPoint(lat=48.30, lon=7.50, brand="Leclerc"),  # loin
    ]
    assert nearest_brand(48.26, 7.45, points) == "Total"


def test_nearest_brand_returns_none_when_all_too_far() -> None:
    points = [BrandPoint(lat=48.30, lon=7.50, brand="Leclerc")]  # ~5 km
    assert nearest_brand(48.26, 7.45, points) is None


def test_nearest_brand_empty_points() -> None:
    assert nearest_brand(48.26, 7.45, []) is None
