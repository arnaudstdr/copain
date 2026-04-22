"""Tests du client Open-Meteo (get_today + get_hourly_precipitation)."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from bot.briefing.weather import DailyWeather, HourlyPrecipitation, OpenMeteoClient


def _fmt(dt: datetime) -> str:
    """Format attendu par Open-Meteo pour `hourly.time` (sans tzinfo)."""
    return dt.strftime("%Y-%m-%dT%H:%M")


async def test_get_hourly_precipitation_filters_past_and_limits_results() -> None:
    tz = ZoneInfo("Europe/Paris")
    now_hour = datetime.now(tz).replace(minute=0, second=0, microsecond=0)
    past = now_hour - timedelta(hours=1)
    h0 = now_hour
    h1 = now_hour + timedelta(hours=1)
    h2 = now_hour + timedelta(hours=2)
    h3 = now_hour + timedelta(hours=3)

    payload = {
        "hourly": {
            "time": [_fmt(past), _fmt(h0), _fmt(h1), _fmt(h2), _fmt(h3)],
            "precipitation": [5.0, 0.0, 0.5, 1.2, 0.0],
            "precipitation_probability": [80, 20, 70, 85, 10],
        }
    }

    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status = MagicMock()

    client = OpenMeteoClient(timezone="Europe/Paris")
    client._client = AsyncMock()
    client._client.get = AsyncMock(return_value=response)

    result = await client.get_hourly_precipitation(lat=48.26, lon=7.45, hours_ahead=3)

    assert len(result) == 3
    assert all(isinstance(item, HourlyPrecipitation) for item in result)
    # L'heure passée est bien filtrée (h0 est la première).
    assert result[0].time == h0.replace(tzinfo=tz)
    assert result[0].mm == 0.0
    assert result[1].time == h1.replace(tzinfo=tz)
    assert result[1].probability_pct == 70
    assert result[2].time == h2.replace(tzinfo=tz)
    assert result[2].mm == 1.2


async def test_get_hourly_precipitation_empty_payload_returns_empty_list() -> None:
    response = MagicMock()
    response.json.return_value = {"hourly": {"time": [], "precipitation": [], "precipitation_probability": []}}
    response.raise_for_status = MagicMock()

    client = OpenMeteoClient(timezone="Europe/Paris")
    client._client = AsyncMock()
    client._client.get = AsyncMock(return_value=response)

    result = await client.get_hourly_precipitation(lat=0.0, lon=0.0)
    assert result == []


async def test_get_forecast_returns_daily_list_with_current_temp_on_day0() -> None:
    payload = {
        "current": {"temperature_2m": 15.3},
        "daily": {
            "time": ["2026-04-22", "2026-04-23", "2026-04-24"],
            "temperature_2m_min": [8.0, 9.5, 10.0],
            "temperature_2m_max": [18.0, 20.0, 22.0],
            "precipitation_sum": [0.0, 2.5, 0.0],
            "weather_code": [1, 61, 3],
            "wind_speed_10m_max": [12.0, 18.0, 10.0],
        },
    }
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status = MagicMock()

    client = OpenMeteoClient(timezone="Europe/Paris")
    client._client = AsyncMock()
    client._client.get = AsyncMock(return_value=response)

    result = await client.get_forecast(lat=48.26, lon=7.45, city="Sélestat", days=3)

    assert len(result) == 3
    assert all(isinstance(d, DailyWeather) for d in result)
    assert result[0].city == "Sélestat"
    assert result[0].temp_current == 15.3
    assert result[0].description == "plutôt dégagé"
    assert result[1].temp_current is None
    assert result[1].description == "pluie faible"
    assert result[1].precipitation_mm == 2.5
    assert result[2].wind_kmh_max == 10.0
