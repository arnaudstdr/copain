"""CRUD async sur la table `tasks` via SQLAlchemy + aiosqlite."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from bot.tasks.models import Base, Task


class TaskManager:
    """Wrapper async autour d'une base SQLite locale."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine: AsyncEngine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}", future=True
        )
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)

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
            return result.scalars().all()

    async def complete(self, task_id: int) -> bool:
        async with self._sessionmaker() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return False
            task.completed = True
            await session.commit()
            return True

    async def delete(self, task_id: int) -> bool:
        async with self._sessionmaker() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return False
            await session.delete(task)
            await session.commit()
            return True

    async def dispose(self) -> None:
        await self._engine.dispose()
