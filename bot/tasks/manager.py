"""CRUD async sur la table `tasks` via SQLAlchemy + aiosqlite."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from bot.tasks.models import Base, Task

if TYPE_CHECKING:
    from bot.tasks.scheduler import ReminderScheduler


class TaskManager:
    """Wrapper async autour d'une base SQLite locale.

    L'engine est injecté depuis `bot/db.py` (partagé avec `FeedManager` pour
    éviter les contentions SQLite).

    Si un `ReminderScheduler` est injecté, `complete()` et `delete()` annulent
    automatiquement le job de rappel associé (évite les rappels fantômes).
    """

    def __init__(self, engine: AsyncEngine, scheduler: ReminderScheduler | None = None) -> None:
        self._engine = engine
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)
        self._scheduler = scheduler

    async def init_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def create(self, content: str, due_at: datetime | None = None) -> Task:
        task = Task(content=content, due_at=due_at)
        async with self._sessionmaker() as session:
            session.add(task)
            await session.commit()
            await session.refresh(task)
        return task

    async def list_pending(self) -> Sequence[Task]:
        async with self._sessionmaker() as session:
            stmt = (
                select(Task)
                .where(Task.completed.is_(False))
                .order_by(Task.due_at.is_(None), Task.due_at, Task.created_at)
            )
            result = await session.execute(stmt)
            return result.scalars().all()  # type: ignore[no-any-return]

    async def complete(self, task_id: int) -> bool:
        async with self._sessionmaker() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return False
            task.completed = True
            await session.commit()
        if self._scheduler is not None:
            self._scheduler.cancel_reminder(task_id)
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
        return True

    async def dispose(self) -> None:
        # L'engine est partagé : c'est main.py qui fait dispose() au shutdown.
        pass
