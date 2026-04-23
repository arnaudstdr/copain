"""Helper httpx avec retry sur erreurs transitoires + parsing JSON.

Partagé par les clients HTTP externes du bot (Open-Meteo, Opendatasoft,
SearXNG, …). Les erreurs réseau/HTTP sont réessayées avec backoff court ;
les payloads non-JSON ne le sont pas (une erreur de parsing ne s'améliore
pas sur retry).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from bot.logging_conf import get_logger

log = get_logger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 2.0)


async def get_json_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    context: str,
    error_cls: type[Exception],
    params: dict[str, Any] | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
) -> Any:
    """GET `url` puis `.json()`, avec retry sur `httpx.HTTPError`.

    - `context` : libellé court (ex. "weather:get_today") inclus dans les
      logs pour faciliter le diagnostic.
    - `error_cls` : exception levée en cas d'échec final (ex. `WeatherError`,
      `FuelError`). Le chaînage `from` préserve l'exception d'origine dans
      `__cause__`.
    - `backoff_seconds` doit contenir au moins `max_attempts - 1` entrées.
    """
    last_exc: httpx.HTTPError | None = None
    for attempt in range(max_attempts):
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            last_exc = exc
            remaining = max_attempts - attempt - 1
            log.warning(
                "http_request_retry",
                context=context,
                attempt=attempt + 1,
                max_attempts=max_attempts,
                exc_type=type(exc).__name__,
                error=str(exc),
                remaining=remaining,
            )
            if remaining == 0:
                break
            await asyncio.sleep(backoff_seconds[attempt])
            continue

        try:
            return response.json()
        except ValueError as exc:
            raise error_cls(f"Réponse non-JSON ({context})") from exc

    assert last_exc is not None
    log.error("http_fetch_failed", context=context, error=str(last_exc))
    raise error_cls(f"Appel HTTP échoué ({context}) : {last_exc}") from last_exc
