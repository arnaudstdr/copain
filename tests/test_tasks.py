"""Tests du TaskManager sur une base SQLite temporaire."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bot.tasks.manager import TaskManager


@pytest.fixture
async def manager(tmp_data_dir: Path) -> TaskManager:
    db_path = tmp_data_dir / "tasks.db"
    mgr = TaskManager(db_path)
    await mgr.init_schema()
    yield mgr
    await mgr.dispose()


async def test_create_task_without_due(manager: TaskManager) -> None:
    task = await manager.create("acheter du pain")
    assert task.id is not None
    assert task.content == "acheter du pain"
    assert task.due_at is None
    assert task.completed is False


async def test_create_task_with_due(manager: TaskManager) -> None:
    due = datetime.now(UTC) + timedelta(hours=3)
    task = await manager.create("appeler dentiste", due_at=due)
    assert task.due_at is not None


async def test_list_pending_orders_by_due(manager: TaskManager) -> None:
    now = datetime.now(UTC)
    await manager.create("sans échéance")
    await manager.create("urgent", due_at=now + timedelta(minutes=10))
    await manager.create("plus tard", due_at=now + timedelta(days=2))

    pending = await manager.list_pending()
    assert len(pending) == 3
    assert pending[0].content == "urgent"
    assert pending[1].content == "plus tard"
    assert pending[2].content == "sans échéance"


async def test_complete_task(manager: TaskManager) -> None:
    task = await manager.create("appeler plombier")
    ok = await manager.complete(task.id)
    assert ok is True
    pending = await manager.list_pending()
    assert all(t.id != task.id for t in pending)


async def test_complete_unknown_task_returns_false(manager: TaskManager) -> None:
    assert await manager.complete(999) is False


async def test_delete_task(manager: TaskManager) -> None:
    task = await manager.create("à supprimer")
    assert await manager.delete(task.id) is True
    assert await manager.delete(task.id) is False
