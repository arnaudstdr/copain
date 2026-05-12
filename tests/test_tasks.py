"""Tests du TaskManager sur une base SQLite temporaire."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.db import create_shared_engine
from bot.tasks.manager import TaskManager


@pytest.fixture
async def manager(tmp_data_dir: Path) -> TaskManager:
    engine = create_shared_engine(tmp_data_dir / "tasks.db")
    mgr = TaskManager(engine)
    await mgr.init_schema()
    yield mgr
    await engine.dispose()


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


async def test_complete_cancels_reminder_when_scheduler_injected(tmp_data_dir: Path) -> None:
    scheduler = MagicMock()
    engine = create_shared_engine(tmp_data_dir / "tasks.db")
    mgr = TaskManager(engine, scheduler=scheduler)
    await mgr.init_schema()
    try:
        task = await mgr.create("appeler dentiste", due_at=datetime.now(UTC) + timedelta(hours=2))
        await mgr.complete(task.id)
        scheduler.cancel_reminder.assert_called_once_with(task.id)
    finally:
        await engine.dispose()


async def test_delete_cancels_reminder_when_scheduler_injected(tmp_data_dir: Path) -> None:
    scheduler = MagicMock()
    engine = create_shared_engine(tmp_data_dir / "tasks.db")
    mgr = TaskManager(engine, scheduler=scheduler)
    await mgr.init_schema()
    try:
        task = await mgr.create("à supprimer", due_at=datetime.now(UTC) + timedelta(hours=2))
        await mgr.delete(task.id)
        scheduler.cancel_reminder.assert_called_once_with(task.id)
    finally:
        await engine.dispose()


async def test_complete_unknown_task_does_not_cancel_reminder(tmp_data_dir: Path) -> None:
    scheduler = MagicMock()
    engine = create_shared_engine(tmp_data_dir / "tasks.db")
    mgr = TaskManager(engine, scheduler=scheduler)
    await mgr.init_schema()
    try:
        assert await mgr.complete(999) is False
        scheduler.cancel_reminder.assert_not_called()
    finally:
        await engine.dispose()


# --- mirror iCloud Rappels -------------------------------------------------


def _mock_reminders_client() -> MagicMock:
    """Client iCloud connecté, méthodes async mockées sans erreur."""
    stub = MagicMock()
    stub.is_connected = True
    stub.push_todo = AsyncMock()
    stub.complete_todo = AsyncMock()
    stub.delete_todo = AsyncMock()
    return stub


async def test_create_pushes_to_icloud_when_mirror_active(tmp_data_dir: Path) -> None:
    reminders = _mock_reminders_client()
    engine = create_shared_engine(tmp_data_dir / "tasks.db")
    mgr = TaskManager(engine, reminders_icloud=reminders)
    await mgr.init_schema()
    try:
        task = await mgr.create("acheter du lait")
        reminders.push_todo.assert_awaited_once_with(task.id, "acheter du lait", None)
    finally:
        await engine.dispose()


async def test_complete_pushes_completion_to_icloud(tmp_data_dir: Path) -> None:
    reminders = _mock_reminders_client()
    engine = create_shared_engine(tmp_data_dir / "tasks.db")
    mgr = TaskManager(engine, reminders_icloud=reminders)
    await mgr.init_schema()
    try:
        task = await mgr.create("X")
        await mgr.complete(task.id)
        reminders.complete_todo.assert_awaited_once_with(task.id)
    finally:
        await engine.dispose()


async def test_complete_is_idempotent_on_already_completed(tmp_data_dir: Path) -> None:
    """Compléter une task déjà completed retourne False et n'appelle pas iCloud."""
    reminders = _mock_reminders_client()
    engine = create_shared_engine(tmp_data_dir / "tasks.db")
    mgr = TaskManager(engine, reminders_icloud=reminders)
    await mgr.init_schema()
    try:
        task = await mgr.create("X")
        assert await mgr.complete(task.id) is True
        reminders.complete_todo.reset_mock()
        # 2e appel : déjà completed, on doit retourner False sans toucher iCloud
        # (le job de sync s'appuie sur ce signal pour ne pas boucler).
        assert await mgr.complete(task.id) is False
        reminders.complete_todo.assert_not_called()
    finally:
        await engine.dispose()


async def test_delete_pushes_deletion_to_icloud(tmp_data_dir: Path) -> None:
    reminders = _mock_reminders_client()
    engine = create_shared_engine(tmp_data_dir / "tasks.db")
    mgr = TaskManager(engine, reminders_icloud=reminders)
    await mgr.init_schema()
    try:
        task = await mgr.create("X")
        await mgr.delete(task.id)
        reminders.delete_todo.assert_awaited_once_with(task.id)
    finally:
        await engine.dispose()


async def test_create_tolerates_icloud_error(tmp_data_dir: Path) -> None:
    """Si le push iCloud échoue, la mutation DB doit quand même réussir."""
    from bot.reminders_icloud.client import ICloudRemindersError

    reminders = _mock_reminders_client()
    reminders.push_todo = AsyncMock(side_effect=ICloudRemindersError("network"))
    engine = create_shared_engine(tmp_data_dir / "tasks.db")
    mgr = TaskManager(engine, reminders_icloud=reminders)
    await mgr.init_schema()
    try:
        task = await mgr.create("survivor")
        # La task est bien créée côté DB malgré l'erreur iCloud.
        assert task.id is not None
        pending = await mgr.list_pending()
        assert any(t.id == task.id for t in pending)
    finally:
        await engine.dispose()


async def test_mirror_skipped_when_client_not_connected(tmp_data_dir: Path) -> None:
    """Si le client n'est pas connecté, on n'essaie même pas de pousser."""
    reminders = _mock_reminders_client()
    reminders.is_connected = False
    engine = create_shared_engine(tmp_data_dir / "tasks.db")
    mgr = TaskManager(engine, reminders_icloud=reminders)
    await mgr.init_schema()
    try:
        await mgr.create("X")
        reminders.push_todo.assert_not_called()
    finally:
        await engine.dispose()
