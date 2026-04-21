"""Client RSS/Atom basé sur feedparser, exécuté en thread pour rester async."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import feedparser

from bot.logging_conf import get_logger
from bot.rss.models import Feed

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class FeedItem:
    feed_name: str
    title: str
    url: str
    summary: str
    published: datetime | None


class RssFetcher:
    """Wrapper async autour de `feedparser.parse` (sync bloquant)."""

    def __init__(self, user_agent: str = "copain-bot/1.0 (+https://github.com)") -> None:
        self._user_agent = user_agent

    async def fetch(self, feed: Feed, limit: int = 10) -> list[FeedItem]:
        parsed = await asyncio.to_thread(
            feedparser.parse, feed.url, agent=self._user_agent
        )
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

    async def fetch_many(
        self, feeds: Sequence[Feed], per_feed: int = 10
    ) -> list[FeedItem]:
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
