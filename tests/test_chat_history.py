"""Tests du ChatHistoryManager sur une base SQLite temporaire."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update

from bot.chat.manager import ChatHistoryManager
from bot.chat.models import ChatMessage
from bot.db import create_shared_engine


@pytest.fixture
async def manager(tmp_data_dir: Path) -> AsyncIterator[ChatHistoryManager]:
    engine = create_shared_engine(tmp_data_dir / "tasks.db")
    mgr = ChatHistoryManager(engine)
    await mgr.init_schema()
    yield mgr
    await engine.dispose()


async def test_add_exchange_persists_two_ordered_rows(manager: ChatHistoryManager) -> None:
    await manager.add_exchange("salut", "bonjour à toi")
    page = await manager.page(limit=10)
    assert [m.role for m in page.messages] == ["user", "assistant"]
    assert [m.content for m in page.messages] == ["salut", "bonjour à toi"]
    # user inséré avant assistant → id strictement croissant.
    assert page.messages[0].id < page.messages[1].id
    assert page.has_more is False


async def test_page_returns_chronological_ascending(manager: ChatHistoryManager) -> None:
    await manager.add_exchange("q1", "r1")
    await manager.add_exchange("q2", "r2")
    page = await manager.page(limit=10)
    assert [m.content for m in page.messages] == ["q1", "r1", "q2", "r2"]


async def test_page_keeps_most_recent_when_limited(manager: ChatHistoryManager) -> None:
    for i in range(3):
        await manager.add_exchange(f"q{i}", f"r{i}")  # 6 lignes au total
    page = await manager.page(limit=4)
    assert page.has_more is True
    # Les 4 plus récentes, en ordre chronologique croissant.
    assert [m.content for m in page.messages] == ["q1", "r1", "q2", "r2"]


async def test_page_paginates_with_before_id(manager: ChatHistoryManager) -> None:
    for i in range(3):
        await manager.add_exchange(f"q{i}", f"r{i}")  # ids 1..6
    recent = await manager.page(limit=4)
    cursor = recent.messages[0].id  # plus ancien chargé (= id 3, "q1")
    older = await manager.page(limit=4, before_id=cursor)
    assert older.has_more is False
    assert [m.content for m in older.messages] == ["q0", "r0"]


async def test_purge_older_than_removes_old_rows(manager: ChatHistoryManager) -> None:
    await manager.add_exchange("vieux", "vieille réponse")
    await manager.add_exchange("récent", "réponse récente")
    # Vieillit artificiellement le 1er échange (40 j) via update direct.
    old = datetime.now(UTC) - timedelta(days=40)
    async with manager._sessionmaker() as session:
        await session.execute(
            update(ChatMessage)
            .where(ChatMessage.content.in_(["vieux", "vieille réponse"]))
            .values(created_at=old)
        )
        await session.commit()

    deleted = await manager.purge_older_than(30)
    assert deleted == 2
    page = await manager.page(limit=10)
    assert [m.content for m in page.messages] == ["récent", "réponse récente"]


async def test_purge_with_non_positive_days_is_noop(manager: ChatHistoryManager) -> None:
    await manager.add_exchange("q", "r")
    assert await manager.purge_older_than(0) == 0
    assert await manager.purge_older_than(-5) == 0
    page = await manager.page(limit=10)
    assert len(page.messages) == 2
