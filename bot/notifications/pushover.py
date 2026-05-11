"""Client Pushover — notifications push iOS."""

from __future__ import annotations

import httpx

from bot.logging_conf import get_logger

log = get_logger(__name__)

PUSHOVER_API = "https://api.pushover.net/1/messages.json"

PRIORITY_LOW = -1
PRIORITY_NORMAL = 0
PRIORITY_HIGH = 1


class PushoverClient:
    def __init__(self, token: str, user: str) -> None:
        self._token = token
        self._user = user

    async def push(
        self,
        message: str,
        title: str = "Copain",
        priority: int = PRIORITY_NORMAL,
        sound: str | None = None,
        url: str | None = None,
        url_title: str | None = None,
    ) -> None:
        if not self._token or not self._user:
            log.warning("pushover_not_configured")
            return

        payload: dict[str, str | int] = {
            "token": self._token,
            "user": self._user,
            "message": message,
            "title": title,
            "priority": priority,
        }
        if sound:
            payload["sound"] = sound
        if url:
            payload["url"] = url
        if url_title:
            payload["url_title"] = url_title

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(PUSHOVER_API, data=payload)
                resp.raise_for_status()
                log.info("pushover_sent", title=title, priority=priority)
        except Exception as exc:
            log.warning("pushover_failed", error=str(exc))
