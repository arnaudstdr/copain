"""Client Open-Meteo pour la météo locale (API publique, sans clé)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from types import TracebackType
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from bot.logging_conf import get_logger

log = get_logger(__name__)

BASE_URL = "https://api.open-meteo.com/v1/forecast"

# Retry sur les erreurs réseau transitoires (timeouts, 5xx, DNS). Les
# backoffs sont volontairement courts : Open-Meteo récupère vite, et on
# ne veut pas retarder le briefing ou la proactivité.
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 2.0)

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


@dataclass(frozen=True, slots=True)
class HourlyPrecipitation:
    """Précipitation prévue à une heure donnée (timezone-aware)."""

    time: datetime
    mm: float
    probability_pct: int


@dataclass(frozen=True, slots=True)
class DailyWeather:
    """Météo quotidienne pour un jour donné (local à la timezone Open-Meteo).

    `temp_current` n'est renseigné que pour aujourd'hui (premier élément du
    forecast) ; `None` pour les jours futurs.
    """

    city: str
    date: date
    temp_min: float
    temp_max: float
    precipitation_mm: float
    wind_kmh_max: float
    description: str
    temp_current: float | None


class WeatherError(RuntimeError):
    """Levée sur erreur HTTP ou payload Open-Meteo inattendu."""


class OpenMeteoClient:
    """Wrapper httpx async autour de l'API Open-Meteo."""

    def __init__(self, timezone: str = "Europe/Paris", timeout: float = 20.0) -> None:
        self._timezone = timezone
        self._client = httpx.AsyncClient(timeout=timeout)

    async def _request_with_retry(self, params: dict[str, Any], context: str) -> dict[str, Any]:
        """GET `BASE_URL` avec retry sur `httpx.HTTPError`.

        Les erreurs réseau/HTTP transitoires sont réessayées jusqu'à
        `_MAX_ATTEMPTS` fois avec un backoff court. Les payloads non-JSON
        ne sont pas réessayés (l'erreur ne s'améliorera pas sur retry).
        """
        last_exc: httpx.HTTPError | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = await self._client.get(BASE_URL, params=params)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                last_exc = exc
                remaining = _MAX_ATTEMPTS - attempt - 1
                log.warning(
                    "weather_request_retry",
                    context=context,
                    attempt=attempt + 1,
                    max_attempts=_MAX_ATTEMPTS,
                    exc_type=type(exc).__name__,
                    error=str(exc),
                    remaining=remaining,
                )
                if remaining == 0:
                    break
                await asyncio.sleep(_BACKOFF_SECONDS[attempt])
                continue

            try:
                return response.json()  # type: ignore[no-any-return]
            except ValueError as exc:
                raise WeatherError("Réponse Open-Meteo non-JSON") from exc

        assert last_exc is not None
        log.error("weather_fetch_failed", context=context, error=str(last_exc))
        raise WeatherError(f"Open-Meteo échoué ({context}) : {last_exc}") from last_exc

    async def get_today(self, lat: float, lon: float, city: str) -> WeatherSummary:
        params: dict[str, Any] = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
            "timezone": self._timezone,
            "forecast_days": 1,
        }
        data = await self._request_with_retry(params, context="get_today")

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

    async def get_forecast(
        self,
        lat: float,
        lon: float,
        city: str,
        days: int = 7,
    ) -> list[DailyWeather]:
        """Retourne les prévisions quotidiennes pour les N prochains jours.

        Le premier élément (jour 0) inclut `temp_current` ; les suivants ont
        `temp_current=None`. Limité à 16 jours par Open-Meteo.
        """
        clamped = max(1, min(days, 16))
        params: dict[str, Any] = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m",
            "daily": (
                "temperature_2m_max,temperature_2m_min,"
                "precipitation_sum,weather_code,wind_speed_10m_max"
            ),
            "timezone": self._timezone,
            "forecast_days": clamped,
        }
        data = await self._request_with_retry(params, context="get_forecast")

        current = data.get("current") or {}
        daily = data.get("daily") or {}
        dates = daily.get("time") or []
        mins = daily.get("temperature_2m_min") or []
        maxs = daily.get("temperature_2m_max") or []
        precs = daily.get("precipitation_sum") or []
        codes = daily.get("weather_code") or []
        winds = daily.get("wind_speed_10m_max") or []

        try:
            current_temp = float(current.get("temperature_2m", 0.0))
        except (TypeError, ValueError) as exc:
            raise WeatherError(f"Payload Open-Meteo malformé : {exc}") from exc

        out: list[DailyWeather] = []
        for i, (d_iso, tmin, tmax, prec, code, wind) in enumerate(
            zip(dates, mins, maxs, precs, codes, winds, strict=False)
        ):
            try:
                d = date.fromisoformat(d_iso)
                out.append(
                    DailyWeather(
                        city=city,
                        date=d,
                        temp_min=float(tmin),
                        temp_max=float(tmax),
                        precipitation_mm=float(prec) if prec is not None else 0.0,
                        wind_kmh_max=float(wind) if wind is not None else 0.0,
                        description=WEATHER_CODES.get(int(code), "indéterminé"),
                        temp_current=current_temp if i == 0 else None,
                    )
                )
            except (TypeError, ValueError) as exc:
                raise WeatherError(f"Payload Open-Meteo malformé : {exc}") from exc
        return out

    async def get_hourly_precipitation(
        self,
        lat: float,
        lon: float,
        hours_ahead: int = 3,
    ) -> list[HourlyPrecipitation]:
        """Retourne les précipitations prévues pour les N prochaines heures.

        Les heures déjà passées (avant l'heure courante tronquée) sont filtrées.
        Les timestamps retournés sont timezone-aware dans `self._timezone`.
        """
        params: dict[str, Any] = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "precipitation,precipitation_probability",
            "timezone": self._timezone,
            "forecast_days": 1,
        }
        data = await self._request_with_retry(params, context="get_hourly_precipitation")

        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []
        precs = hourly.get("precipitation") or []
        probas = hourly.get("precipitation_probability") or []

        tz = ZoneInfo(self._timezone)
        now_hour = datetime.now(tz).replace(minute=0, second=0, microsecond=0)
        out: list[HourlyPrecipitation] = []
        for iso, mm, proba in zip(times, precs, probas, strict=False):
            try:
                t = datetime.fromisoformat(iso).replace(tzinfo=tz)
            except (TypeError, ValueError):
                continue
            if t < now_hour:
                continue
            out.append(
                HourlyPrecipitation(
                    time=t,
                    mm=float(mm) if mm is not None else 0.0,
                    probability_pct=int(proba) if proba is not None else 0,
                )
            )
            if len(out) >= hours_ahead:
                break
        return out

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
