"""Tests du FuelClient (data.economie.gouv.fr)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from bot.fuel.client import FuelClient, FuelError
from bot.fuel.models import GeoPoint


def _payload(records: list[dict]) -> dict:
    return {"total_count": len(records), "results": records}


def _record(
    *,
    rec_id: str = "12345",
    adresse: str = "1 rue du Pont",
    ville: str = "Sélestat",
    cp: str = "67600",
    lat: float = 48.26,
    lon: float = 7.45,
    gazole_prix: float | None = 1.689,
    gazole_maj: str | None = "2026-04-21T14:32:00+02:00",
) -> dict:
    rec: dict = {
        "id": rec_id,
        "adresse": adresse,
        "ville": ville,
        "cp": cp,
        "geom": {"type": "Point", "coordinates": [lon, lat]},
    }
    if gazole_prix is not None:
        rec["gazole_prix"] = gazole_prix
    if gazole_maj is not None:
        rec["gazole_maj"] = gazole_maj
    return rec


async def test_find_cheapest_returns_stations_sorted_with_distance() -> None:
    response = MagicMock()
    response.json.return_value = _payload(
        [
            _record(rec_id="A", gazole_prix=1.659, lat=48.26, lon=7.45),
            _record(rec_id="B", gazole_prix=1.701, lat=48.30, lon=7.50),
        ]
    )
    response.raise_for_status = MagicMock()

    client = FuelClient()
    client._client = AsyncMock()
    client._client.get = AsyncMock(return_value=response)

    center = GeoPoint(lat=48.26, lon=7.45)
    stations = await client.find_cheapest("gazole", center, radius_km=10.0, limit=5)

    assert len(stations) == 2
    assert stations[0].id == "A"
    assert stations[0].price_eur == pytest.approx(1.659)
    assert stations[0].distance_km == pytest.approx(0.0, abs=0.01)
    assert stations[1].distance_km > 0
    assert isinstance(stations[0].updated_at, datetime)


async def test_find_cheapest_passes_within_distance_and_select() -> None:
    response = MagicMock()
    response.json.return_value = _payload([])
    response.raise_for_status = MagicMock()

    client = FuelClient()
    client._client = AsyncMock()
    client._client.get = AsyncMock(return_value=response)

    await client.find_cheapest("sp98", GeoPoint(lat=48.58, lon=7.75), radius_km=5.0, limit=3)

    call = client._client.get.call_args
    params = call.kwargs["params"]
    assert "within_distance(geom, geom'POINT(7.75 48.58)', 5.0km)" in params["where"]
    assert "sp98_prix is not null" in params["where"]
    assert params["order_by"] == "sp98_prix asc"
    assert params["limit"] == 3
    assert "sp98_prix" in params["select"]
    assert "sp98_maj" in params["select"]


async def test_find_cheapest_returns_empty_when_no_results() -> None:
    response = MagicMock()
    response.json.return_value = _payload([])
    response.raise_for_status = MagicMock()

    client = FuelClient()
    client._client = AsyncMock()
    client._client.get = AsyncMock(return_value=response)

    stations = await client.find_cheapest("e85", GeoPoint(lat=48.0, lon=7.0), radius_km=10.0)
    assert stations == []


async def test_find_cheapest_http_error_raises_fuel_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("bot.http_retry.asyncio.sleep", AsyncMock())
    client = FuelClient()
    client._client = AsyncMock()
    client._client.get = AsyncMock(side_effect=httpx.ConnectTimeout("boom"))

    with pytest.raises(FuelError, match="fuel:find_cheapest") as excinfo:
        await client.find_cheapest("gazole", GeoPoint(lat=48.0, lon=7.0), radius_km=10.0)

    assert isinstance(excinfo.value.__cause__, httpx.ConnectTimeout)
    assert client._client.get.await_count == 3


async def test_find_cheapest_skips_records_without_geom() -> None:
    broken = {
        "id": "X",
        "adresse": "nowhere",
        "ville": "X",
        "cp": "00000",
        "gazole_prix": 1.50,
    }  # pas de geom
    response = MagicMock()
    response.json.return_value = _payload([broken, _record(rec_id="Y", gazole_prix=1.60)])
    response.raise_for_status = MagicMock()

    client = FuelClient()
    client._client = AsyncMock()
    client._client.get = AsyncMock(return_value=response)

    stations = await client.find_cheapest("gazole", GeoPoint(lat=48.26, lon=7.45), radius_km=10.0)
    assert [s.id for s in stations] == ["Y"]


async def test_find_cheapest_accepts_legacy_geom_lat_lon_dict() -> None:
    """Le dataset peut renvoyer {'lat': X, 'lon': Y} au lieu d'un GeoJSON Point."""
    rec = {
        "id": "Z",
        "adresse": "rue A",
        "ville": "Z",
        "cp": "00000",
        "geom": {"lat": 48.26, "lon": 7.45},
        "gazole_prix": 1.70,
        "gazole_maj": "2026-04-21T10:00:00+02:00",
    }
    response = MagicMock()
    response.json.return_value = _payload([rec])
    response.raise_for_status = MagicMock()

    client = FuelClient()
    client._client = AsyncMock()
    client._client.get = AsyncMock(return_value=response)

    stations = await client.find_cheapest("gazole", GeoPoint(lat=48.26, lon=7.45), radius_km=10.0)
    assert len(stations) == 1
    assert stations[0].lat == pytest.approx(48.26)
    assert stations[0].lon == pytest.approx(7.45)
