"""Tests du NominatimClient (géocoding OSM limité à la France)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from bot.fuel.geocoding import NominatimClient, NominatimError


def _ok_response(payload: object) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response


async def test_geocode_fr_parses_first_result() -> None:
    client = NominatimClient(user_agent="test/1.0")
    client._client = AsyncMock()
    client._client.get = AsyncMock(
        return_value=_ok_response(
            [{"lat": "48.08", "lon": "7.36", "display_name": "Colmar, Haut-Rhin"}]
        )
    )

    point = await client.geocode_fr("Colmar")
    assert point is not None
    assert point.lat == pytest.approx(48.08)
    assert point.lon == pytest.approx(7.36)


async def test_geocode_fr_passes_user_agent_and_countrycodes() -> None:
    client = NominatimClient(user_agent="copain-bot/1.0 (contact)")
    assert client._client.headers.get("User-Agent") == "copain-bot/1.0 (contact)"

    await client.aclose()
    # Vérifie aussi la présence de countrycodes dans l'appel
    client2 = NominatimClient(user_agent="test/1.0")
    client2._client = AsyncMock()
    client2._client.get = AsyncMock(return_value=_ok_response([{"lat": "48.0", "lon": "7.0"}]))
    await client2.geocode_fr("Strasbourg")
    params = client2._client.get.call_args.kwargs["params"]
    assert params["countrycodes"] == "fr"
    assert params["format"] == "json"


async def test_geocode_fr_returns_none_on_empty_payload() -> None:
    client = NominatimClient(user_agent="test/1.0")
    client._client = AsyncMock()
    client._client.get = AsyncMock(return_value=_ok_response([]))
    assert await client.geocode_fr("Ville inconnue xyz") is None


async def test_geocode_fr_uses_cache_on_second_call() -> None:
    client = NominatimClient(user_agent="test/1.0")
    client._client = AsyncMock()
    client._client.get = AsyncMock(return_value=_ok_response([{"lat": "48.58", "lon": "7.75"}]))

    await client.geocode_fr("Strasbourg")
    await client.geocode_fr("Strasbourg")  # doit hit le cache
    await client.geocode_fr("strasbourg")  # même clé (lowercase)
    assert client._client.get.call_count == 1


async def test_geocode_fr_http_error_raises_nominatim_error() -> None:
    client = NominatimClient(user_agent="test/1.0")
    client._client = AsyncMock()
    client._client.get = AsyncMock(side_effect=httpx.ConnectTimeout("boom"))
    with pytest.raises(NominatimError, match="Nominatim"):
        await client.geocode_fr("Paris")


async def test_geocode_fr_empty_query_returns_none_without_http_call() -> None:
    client = NominatimClient(user_agent="test/1.0")
    client._client = AsyncMock()
    client._client.get = AsyncMock()
    assert await client.geocode_fr("   ") is None
    client._client.get.assert_not_called()
