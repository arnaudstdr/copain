"""CRUD async sur la table `thoughts` via SQLAlchemy + aiosqlite."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from bot.tasks.models import Base
from bot.thoughts.models import THOUGHT_KINDS, Thought


class ThoughtManager:
    """Wrapper async autour de la table SQLite `thoughts`.

    L'engine est partagé avec `TaskManager` / `FeedManager` /
    `NotificationStore` (cf. `bot.db.create_shared_engine`). Aucun
    scheduler n'est rattaché — un dépôt ne déclenche pas de rappel
    différé : l'idée est justement de **ne pas** renvoyer la pensée à
    l'utilisateur, mais de la garder accessible à la demande.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)

    async def init_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def create(self, content: str, kind: str | None = None) -> Thought:
        """Insère une pensée. `kind` est validé contre `THOUGHT_KINDS` ou laissé null."""
        if kind is not None and kind not in THOUGHT_KINDS:
            raise ValueError(f"kind invalide : {kind!r}")
        thought = Thought(content=content, kind=kind)
        async with self._sessionmaker() as session:
            session.add(thought)
            await session.commit()
            await session.refresh(thought)
        return thought

    async def list_recent(self, limit: int = 50) -> Sequence[Thought]:
        """Retourne les dépôts les plus récents (ordre chronologique inverse)."""
        async with self._sessionmaker() as session:
            stmt = select(Thought).order_by(Thought.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            return result.scalars().all()  # type: ignore[no-any-return, unused-ignore]

    async def list_since(self, since: datetime, limit: int = 200) -> Sequence[Thought]:
        """Retourne les dépôts créés depuis `since` (chronologique inverse)."""
        async with self._sessionmaker() as session:
            stmt = (
                select(Thought)
                .where(Thought.created_at >= since)
                .order_by(Thought.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return result.scalars().all()  # type: ignore[no-any-return, unused-ignore]
