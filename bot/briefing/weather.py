"""Client Open-Meteo pour la météo locale (API publique, sans clé)."""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Any

import httpx

from bot.logging_conf import get_logger

log = get_logger(__name__)

BASE_URL = "https://api.open-meteo.com/v1/forecast"

# Mapping des codes météo WMO → description FR (codes utilisés par Open-Meteo).
# Référence : https://open-meteo.com/en/docs
WEATHER_CODES: dict[int, str] = {
    0: "ciel dégagé",
    1: "plutôt dégagé",
    2: "partiellement nuageux",
    3: "couvert",
    45: "brouillard",
    48: "brouillard givrant",
    51: "bruine légère",
    53: "bruine modérée",
    55: "bruine dense",
    56: "bruine verglaçante légère",
    57: "bruine verglaçante dense",
    61: "pluie faible",
    63: "pluie modérée",
    65: "pluie forte",
    66: "pluie verglaçante légère",
    67: "pluie verglaçante forte",
    71: "neige faible",
    73: "neige modérée",
    75: "neige forte",
    77: "grains de neige",
    80: "averses faibles",
    81: "averses modérées",
    82: "averses violentes",
    85: "averses de neige faibles",
    86: "averses de neige fortes",
    95: "orage",
    96: "orage avec grêle faible",
    99: "orage avec grêle forte",
}


@dataclass(frozen=True, slots=True)
class WeatherSummary:
    city: str
    temp_current: float
    temp_min: float
    temp_max: float
    precipitation_mm: float
    wind_kmh: float
    description: str


class WeatherError(RuntimeError):
    """Levée sur erreur HTTP ou payload Open-Meteo inattendu."""


class OpenMeteoClient:
    """Wrapper httpx async autour de l'API Open-Meteo."""

    def __init__(self, timezone: str = "Europe/Paris", timeout: float = 10.0) -> None:
        self._timezone = timezone
        self._client = httpx.AsyncClient(timeout=timeout)

    async def get_today(self, lat: float, lon: float, city: str) -> WeatherSummary:
        params: dict[str, Any] = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
            "timezone": self._timezone,
            "forecast_days": 1,
        }
        try:
            response = await self._client.get(BASE_URL, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("weather_fetch_failed", lat=lat, lon=lon, error=str(exc))
            raise WeatherError(f"Open-Meteo échoué : {exc}") from exc

        try:
            data: dict[str, Any] = response.json()
        except ValueError as exc:
            raise WeatherError("Réponse Open-Meteo non-JSON") from exc

        current = data.get("current") or {}
        daily = data.get("daily") or {}

        try:
            code = int((daily.get("weather_code") or [0])[0])
            return WeatherSummary(
                city=city,
                temp_current=float(current.get("temperature_2m", 0.0)),
                temp_min=float((daily.get("temperature_2m_min") or [0.0])[0]),
                temp_max=float((daily.get("temperature_2m_max") or [0.0])[0]),
                precipitation_mm=float((daily.get("precipitation_sum") or [0.0])[0]),
                wind_kmh=float(current.get("wind_speed_10m", 0.0)),
                description=WEATHER_CODES.get(code, "indéterminé"),
            )
        except (TypeError, ValueError, IndexError) as exc:
            raise WeatherError(f"Payload Open-Meteo malformé : {exc}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> OpenMeteoClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
