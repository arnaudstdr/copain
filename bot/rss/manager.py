"""CRUD async des flux RSS stockés en SQLite."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from bot.logging_conf import get_logger
from bot.rss.models import Feed
from bot.tasks.models import Base

log = get_logger(__name__)


class FeedAlreadyExists(ValueError):
    """Levée quand on tente d'ajouter un flux dont l'URL ou le nom existe déjà."""


class FeedManager:
    """Gère les entrées de la table `feeds`.

    L'engine est partagé avec `TaskManager` (cf. `bot/db.py`) pour éviter
    d'avoir deux pools concurrents sur le même fichier SQLite.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)

    async def init_schema(self) -> None:
        """No-op si le schéma a déjà été créé par TaskManager (Base partagée)."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def add(self, url: str, name: str, category: str = "general") -> Feed:
        feed = Feed(url=url, name=name, category=category)
        async with self._sessionmaker() as session:
            session.add(feed)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise FeedAlreadyExists(f"Flux déjà présent (url ou nom) : {name} / {url}") from exc
            await session.refresh(feed)
        log.info("feed_added", feed_id=feed.id, name=name, url=url)
        return feed

    async def list(self, enabled_only: bool = True) -> Sequence[Feed]:
        async with self._sessionmaker() as session:
            stmt = select(Feed).order_by(Feed.name)
            if enabled_only:
                stmt = stmt.where(Feed.enabled.is_(True))
            result = await session.execute(stmt)
            return result.scalars().all()

    async def get(self, name_or_id: str | int) -> Feed | None:
        async with self._sessionmaker() as session:
            if isinstance(name_or_id, int):
                return await session.get(Feed, name_or_id)
            # Échappe % et _ pour éviter qu'un nom utilisateur style "zd%" fasse
            # un match sauvage (les wildcards LIKE sont réservés).
            pattern = _escape_like(name_or_id)
            stmt = select(Feed).where(
                or_(
                    Feed.name == name_or_id,
                    Feed.name.ilike(f"%{pattern}%", escape="\\"),
                )
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def remove(self, name_or_id: str | int) -> bool:
        async with self._sessionmaker() as session:
            feed = await self._load(session, name_or_id)
            if feed is None:
                return False
            await session.delete(feed)
            await session.commit()
        log.info("feed_removed", name_or_id=name_or_id)
        return True

    async def toggle(self, name_or_id: str | int, enabled: bool) -> bool:
        async with self._sessionmaker() as session:
            feed = await self._load(session, name_or_id)
            if feed is None:
                return False
            feed.enabled = enabled
            await session.commit()
        log.info("feed_toggled", name_or_id=name_or_id, enabled=enabled)
        return True

    async def count(self) -> int:
        async with self._sessionmaker() as session:
            result = await session.execute(select(Feed))
            return len(result.scalars().all())

    async def dispose(self) -> None:
        # L'engine est partagé : c'est main.py qui fait dispose() au shutdown.
        pass

    async def _load(self, session: AsyncSession, name_or_id: str | int) -> Feed | None:
        if isinstance(name_or_id, int):
            feed: Feed | None = await session.get(Feed, name_or_id)
            return feed
        stmt = select(Feed).where(Feed.name == name_or_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


def _escape_like(value: str) -> str:
    """Échappe les wildcards LIKE (% et _) et le caractère d'échappement \\."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
