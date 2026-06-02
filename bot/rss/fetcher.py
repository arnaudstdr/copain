"""Client RSS/Atom : téléchargement httpx borné + parsing feedparser en thread.

Le téléchargement est fait par httpx (timeout + limite de taille) plutôt que
de laisser `feedparser.parse(url)` faire son propre fetch sans garde-fou :
un flux gigantesque ou un serveur qui ne répond pas bloquerait sinon le
thread du pool sans limite. Le parsing (sync bloquant) reste exécuté via
`asyncio.to_thread`, sur un contenu déjà borné.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx

from bot.logging_conf import get_logger
from bot.rss.models import Feed

log = get_logger(__name__)

# Garde-fous par défaut : un flux RSS légitime fait quelques dizaines de Ko,
# 5 Mo laisse une marge très large (gros flux Atom avec contenu inline).
DEFAULT_TIMEOUT_SEC = 10.0
DEFAULT_MAX_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class FeedItem:
    feed_name: str
    title: str
    url: str
    summary: str
    published: datetime | None


class RssFetcher:
    """Téléchargement httpx (timeout + taille max) puis `feedparser.parse`."""

    def __init__(
        self,
        user_agent: str = "copain-bot/1.0 (+https://github.com)",
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        max_bytes: int = DEFAULT_MAX_BYTES,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._timeout_sec = timeout_sec
        self._max_bytes = max_bytes
        # Injectable pour les tests (httpx.MockTransport), None en prod.
        self._transport = transport

    async def fetch(self, feed: Feed, limit: int = 10) -> list[FeedItem]:
        try:
            content = await self._download(feed.url)
        except httpx.HTTPError as exc:
            log.warning("rss_fetch_failed", feed=feed.name, error=str(exc))
            return []
        except FeedTooLargeError as exc:
            log.warning("rss_fetch_too_large", feed=feed.name, error=str(exc))
            return []

        parsed = await asyncio.to_thread(feedparser.parse, content)
        if parsed.bozo and not parsed.entries:
            log.warning("rss_fetch_failed", feed=feed.name, error=str(parsed.bozo_exception))
            return []

        items: list[FeedItem] = []
        for entry in parsed.entries[:limit]:
            items.append(
                FeedItem(
                    feed_name=feed.name,
                    title=_safe_str(entry, "title", "(sans titre)"),
                    url=_safe_str(entry, "link", ""),
                    summary=_safe_str(entry, "summary", ""),
                    published=_parse_published(entry),
                )
            )
        log.info("rss_fetched", feed=feed.name, count=len(items))
        return items

    async def _download(self, url: str) -> bytes:
        """Télécharge le flux en streaming, borné à `max_bytes`.

        Lève `FeedTooLargeError` dès que la limite est dépassée — on n'attend
        pas la fin du téléchargement pour rejeter un flux trop gros.
        """
        async with (
            httpx.AsyncClient(
                timeout=self._timeout_sec,
                headers={"User-Agent": self._user_agent},
                follow_redirects=True,
                transport=self._transport,
            ) as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > self._max_bytes:
                    raise FeedTooLargeError(f"flux > {self._max_bytes} octets ({url})")
                chunks.append(chunk)
        return b"".join(chunks)

    async def fetch_many(self, feeds: Sequence[Feed], per_feed: int = 10) -> list[FeedItem]:
        if not feeds:
            return []
        results = await asyncio.gather(
            *(self.fetch(f, per_feed) for f in feeds),
            return_exceptions=True,
        )
        flat: list[FeedItem] = []
        for res in results:
            if isinstance(res, list):
                flat.extend(res)
        flat.sort(
            key=lambda it: it.published or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return flat


class FeedTooLargeError(RuntimeError):
    """Levée quand un flux dépasse la taille maximale autorisée."""


def _safe_str(entry: Any, key: str, default: str) -> str:
    value = entry.get(key, default)
    if not isinstance(value, str):
        return default
    return value.strip()


def _parse_published(entry: Any) -> datetime | None:
    parsed_time = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed_time is None:
        return None
    try:
        return datetime(
            parsed_time[0],
            parsed_time[1],
            parsed_time[2],
            parsed_time[3],
            parsed_time[4],
            parsed_time[5],
            tzinfo=UTC,
        )
    except (TypeError, ValueError):
        return None
