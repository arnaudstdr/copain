"""Tests du `RssFetcher` : téléchargement borné httpx + parsing feedparser.

Le transport httpx est mocké (`httpx.MockTransport`) : aucun réseau réel.
"""

from __future__ import annotations

import httpx

from bot.rss.fetcher import RssFetcher
from bot.rss.models import Feed

_RSS_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Exemple</title>
    <item>
      <title>Premier article</title>
      <link>https://example.org/a</link>
      <description>Le contenu du premier article.</description>
      <pubDate>Mon, 01 Jun 2026 08:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Second article</title>
      <link>https://example.org/b</link>
      <description>Le contenu du second article.</description>
    </item>
  </channel>
</rss>
"""


def _feed() -> Feed:
    feed = Feed(name="exemple", url="https://example.org/rss.xml", category="tech")
    return feed


def _fetcher_with(handler: httpx.MockTransport, **kwargs: object) -> RssFetcher:
    return RssFetcher(transport=handler, **kwargs)  # type: ignore[arg-type]


async def test_fetch_parses_entries() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=_RSS_SAMPLE))
    fetcher = _fetcher_with(transport)
    items = await fetcher.fetch(_feed())
    assert len(items) == 2
    assert items[0].title == "Premier article"
    assert items[0].url == "https://example.org/a"
    assert items[0].published is not None
    assert items[1].published is None


async def test_fetch_respects_limit() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=_RSS_SAMPLE))
    fetcher = _fetcher_with(transport)
    items = await fetcher.fetch(_feed(), limit=1)
    assert len(items) == 1


async def test_fetch_http_error_returns_empty() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    fetcher = _fetcher_with(transport)
    assert await fetcher.fetch(_feed()) == []


async def test_fetch_network_error_returns_empty() -> None:
    def raise_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connexion impossible")

    transport = httpx.MockTransport(raise_timeout)
    fetcher = _fetcher_with(transport)
    assert await fetcher.fetch(_feed()) == []


async def test_fetch_too_large_feed_rejected() -> None:
    """Un flux dépassant max_bytes est rejeté sans être parsé."""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 2048))
    fetcher = _fetcher_with(transport, max_bytes=1024)
    assert await fetcher.fetch(_feed()) == []


async def test_fetch_invalid_xml_returns_empty() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"pas du xml du tout {")
    )
    fetcher = _fetcher_with(transport)
    assert await fetcher.fetch(_feed()) == []


async def test_fetch_many_aggregates_and_sorts() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=_RSS_SAMPLE))
    fetcher = _fetcher_with(transport)
    feeds = [_feed(), Feed(name="autre", url="https://example.org/2.xml", category="tech")]
    items = await fetcher.fetch_many(feeds, per_feed=2)
    assert len(items) == 4
    # Tri anté-chronologique : les items datés en premier.
    assert items[0].published is not None
