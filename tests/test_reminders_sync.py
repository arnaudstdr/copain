"""Tests du job de sync iCloud → DB locale."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from bot.db import create_shared_engine
from bot.reminders_icloud.sync import sync_completed_tasks
from bot.tasks.manager import TaskManager


@pytest.fixture
async def engine(tmp_data_dir: Path) -> AsyncIterator[AsyncEngine]:
    eng = create_shared_engine(tmp_data_dir / "tasks.db")
    yield eng
    await eng.dispose()


@pytest.fixture
async def tasks_mgr(engine: AsyncEngine) -> TaskManager:
    mgr = TaskManager(engine)
    await mgr.init_schema()
    return mgr


@pytest.fixture
def reminders_client() -> MagicMock:
    """Client iCloud mocké (connecté par défaut, completed=[])."""
    stub = MagicMock()
    stub.is_connected = True
    stub.list_completed_uids = AsyncMock(return_value=[])
    return stub


async def test_sync_marks_pending_tasks_as_completed(
    tasks_mgr: TaskManager, reminders_client: MagicMock
) -> None:
    t1 = await tasks_mgr.create("Task 1")
    t2 = await tasks_mgr.create("Task 2")
    reminders_client.list_completed_uids = AsyncMock(return_value=[t1.id, t2.id])

    count = await sync_completed_tasks(tasks_mgr, reminders_client)

    assert count == 2
    pending = await tasks_mgr.list_pending()
    assert pending == []


async def test_sync_idempotent_already_completed_returns_zero(
    tasks_mgr: TaskManager, reminders_client: MagicMock
) -> None:
    """Une task déjà completed côté DB n'est pas recomptée."""
    t = await tasks_mgr.create("Already done")
    await tasks_mgr.complete(t.id)
    reminders_client.list_completed_uids = AsyncMock(return_value=[t.id])

    count = await sync_completed_tasks(tasks_mgr, reminders_client)
    assert count == 0


async def test_sync_skips_when_not_connected(
    tasks_mgr: TaskManager, reminders_client: MagicMock
) -> None:
    reminders_client.is_connected = False
    count = await sync_completed_tasks(tasks_mgr, reminders_client)
    assert count == 0
    reminders_client.list_completed_uids.assert_not_called()


async def test_sync_ignores_unknown_task_ids(
    tasks_mgr: TaskManager, reminders_client: MagicMock
) -> None:
    """Un task_id absent côté DB (supprimé entre-temps) est silencieusement ignoré."""
    reminders_client.list_completed_uids = AsyncMock(return_value=[999])
    count = await sync_completed_tasks(tasks_mgr, reminders_client)
    assert count == 0
