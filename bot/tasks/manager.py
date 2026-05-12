"""CRUD async sur la table `tasks` via SQLAlchemy + aiosqlite."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from bot.logging_conf import get_logger
from bot.tasks.models import Base, Task

if TYPE_CHECKING:
    from bot.reminders_icloud.client import ICloudRemindersClient
    from bot.tasks.scheduler import ReminderScheduler

log = get_logger(__name__)


class TaskManager:
    """Wrapper async autour d'une base SQLite locale.

    L'engine est injecté depuis `bot/db.py` (partagé avec `FeedManager` pour
    éviter les contentions SQLite).

    Si un `ReminderScheduler` est injecté, `complete()` et `delete()` annulent
    automatiquement le job de rappel associé (évite les rappels fantômes).

    Si un `ICloudRemindersClient` est injecté, chaque mutation est aussi
    miroitée vers Apple Rappels (one-way DB → iCloud). Les erreurs CalDAV
    sont loggées en warning mais ne propagent jamais : la DB reste source
    de vérité, le mirror est best-effort.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        scheduler: ReminderScheduler | None = None,
        reminders_icloud: ICloudRemindersClient | None = None,
    ) -> None:
        self._engine = engine
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)
        self._scheduler = scheduler
        self._reminders_icloud = reminders_icloud

    async def init_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def create(self, content: str, due_at: datetime | None = None) -> Task:
        task = Task(content=content, due_at=due_at)
        async with self._sessionmaker() as session:
            session.add(task)
            await session.commit()
            await session.refresh(task)
        await self._push_to_icloud(task.id, content, due_at)
        return task

    async def list_pending(self) -> Sequence[Task]:
        async with self._sessionmaker() as session:
            stmt = (
                select(Task)
                .where(Task.completed.is_(False))
                .order_by(Task.due_at.is_(None), Task.due_at, Task.created_at)
            )
            result = await session.execute(stmt)
            return result.scalars().all()  # type: ignore[no-any-return, unused-ignore]

    async def complete(self, task_id: int) -> bool:
        """Marque comme completed. Idempotent : early return False si déjà fait.

        Le retour False sur task déjà completed évite que le job de sync
        iCloud rentre en boucle (chaque cycle, le VTODO reste "completed"
        côté iOS — on doit pouvoir détecter "rien à faire" rapidement).
        """
        async with self._sessionmaker() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return False
            if task.completed:
                return False
            task.completed = True
            await session.commit()
        if self._scheduler is not None:
            self._scheduler.cancel_reminder(task_id)
        await self._complete_in_icloud(task_id)
        return True

    async def delete(self, task_id: int) -> bool:
        async with self._sessionmaker() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return False
            await session.delete(task)
            await session.commit()
        if self._scheduler is not None:
            self._scheduler.cancel_reminder(task_id)
        await self._delete_in_icloud(task_id)
        return True

    async def _push_to_icloud(self, task_id: int, content: str, due_at: datetime | None) -> None:
        if self._reminders_icloud is None or not self._reminders_icloud.is_connected:
            return
        from bot.reminders_icloud.client import ICloudRemindersError

        try:
            await self._reminders_icloud.push_todo(task_id, content, due_at)
        except ICloudRemindersError as exc:
            log.warning("reminders_push_failed", task_id=task_id, error=str(exc))

    async def _complete_in_icloud(self, task_id: int) -> None:
        if self._reminders_icloud is None or not self._reminders_icloud.is_connected:
            return
        from bot.reminders_icloud.client import ICloudRemindersError

        try:
            await self._reminders_icloud.complete_todo(task_id)
        except ICloudRemindersError as exc:
            log.warning("reminders_complete_failed", task_id=task_id, error=str(exc))

    async def _delete_in_icloud(self, task_id: int) -> None:
        if self._reminders_icloud is None or not self._reminders_icloud.is_connected:
            return
        from bot.reminders_icloud.client import ICloudRemindersError

        try:
            await self._reminders_icloud.delete_todo(task_id)
        except ICloudRemindersError as exc:
            log.warning("reminders_delete_failed", task_id=task_id, error=str(exc))

    async def dispose(self) -> None:
        # L'engine est partagé : c'est main.py qui fait dispose() au shutdown.
        pass
