"""Tests du FeedManager sur une base SQLite temporaire."""

from __future__ import annotations

from pathlib import Path

import pytest

from bot.rss.manager import FeedAlreadyExists, FeedManager


@pytest.fixture
async def manager(tmp_data_dir: Path) -> FeedManager:
    db_path = tmp_data_dir / "feeds.db"
    mgr = FeedManager(db_path)
    await mgr.init_schema()
    yield mgr
    await mgr.dispose()


async def test_add_feed(manager: FeedManager) -> None:
    feed = await manager.add(
        url="https://example.com/feed.xml", name="Example", category="tech"
    )
    assert feed.id is not None
    assert feed.name == "Example"
    assert feed.category == "tech"
    assert feed.enabled is True


async def test_add_duplicate_raises(manager: FeedManager) -> None:
    await manager.add(url="https://example.com/feed.xml", name="Example")
    with pytest.raises(FeedAlreadyExists):
        await manager.add(url="https://example.com/feed.xml", name="Example")


async def test_list_enabled_only_filters(manager: FeedManager) -> None:
    await manager.add(url="https://a.com/rss", name="A")
    await manager.add(url="https://b.com/rss", name="B")
    await manager.toggle("B", enabled=False)

    enabled = await manager.list(enabled_only=True)
    assert [f.name for f in enabled] == ["A"]

    all_feeds = await manager.list(enabled_only=False)
    assert {f.name for f in all_feeds} == {"A", "B"}


async def test_get_by_name_exact_and_partial(manager: FeedManager) -> None:
    await manager.add(url="https://theverge.com/rss", name="The Verge")
    exact = await manager.get("The Verge")
    assert exact is not None and exact.name == "The Verge"

    partial = await manager.get("verge")
    assert partial is not None and partial.name == "The Verge"


async def test_remove_feed(manager: FeedManager) -> None:
    await manager.add(url="https://example.com/rss", name="Example")
    assert await manager.remove("Example") is True
    assert await manager.remove("Example") is False


async def test_count_feeds(manager: FeedManager) -> None:
    assert await manager.count() == 0
    await manager.add(url="https://a.com/rss", name="A")
    await manager.add(url="https://b.com/rss", name="B")
    assert await manager.count() == 2
