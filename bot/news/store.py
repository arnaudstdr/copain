"""CRUD async sur la table `news_digests` via SQLAlchemy + aiosqlite.

Le store persiste le digest actu de la journée en cours et en garantit
l'unicité (`save` remplace tout). `get` normalise `fetched_at` en aware UTC
à la lecture — aiosqlite rend des `datetime` naïfs même sur une colonne
`DateTime(timezone=True)`.
"""

from __future__ import annotations

from datetime import UTC, date
from typing import cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from bot.logging_conf import get_logger
from bot.news.models import NewsDigest
from bot.tasks.models import Base

log = get_logger(__name__)


class NewsDigestStore:
    """Wrapper async autour de la table SQLite `news_digests`.

    L'engine est partagé avec les autres managers de `tasks.db` (cf.
    `bot.db.create_shared_engine`). La table ne conserve qu'un digest à la
    fois : celui de la journée civile en cours.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)

    async def init_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def get(self, day: date) -> NewsDigest | None:
        """Retourne le digest de `day` s'il existe, sinon `None`.

        `fetched_at` est re-tagué aware UTC à la lecture (aiosqlite rend des
        naïfs) pour que la sérialisation API produise un offset `+00:00`.
        """
        async with self._sessionmaker() as session:
            stmt = select(NewsDigest).where(NewsDigest.digest_date == day.isoformat())
            result = await session.execute(stmt)
            digest: NewsDigest | None = result.scalar_one_or_none()
        if digest is not None and digest.fetched_at.tzinfo is None:
            digest.fetched_at = digest.fetched_at.replace(tzinfo=UTC)
        return digest

    async def save(self, day: date, markdown: str) -> NewsDigest:
        """Remplace tout le contenu de la table par le digest de `day`.

        Suppression + insertion dans la même transaction : la table ne garde
        jamais plus d'un digest (cf. SPEC décision 2). Retourne l'objet inséré.
        """
        digest = NewsDigest(digest_date=day.isoformat(), markdown=markdown)
        async with self._sessionmaker() as session:
            await session.execute(delete(NewsDigest))
            session.add(digest)
            await session.commit()
        log.info("news_digest_saved", digest_date=day.isoformat())
        return digest

    async def purge_except(self, day: date) -> int:
        """Supprime les digests dont la date diffère de `day`.

        Retourne le nombre de lignes supprimées ; log `news_digest_purged`
        si au moins une ligne a été retirée.
        """
        async with self._sessionmaker() as session:
            result = await session.execute(
                delete(NewsDigest).where(NewsDigest.digest_date != day.isoformat())
            )
            await session.commit()
        deleted = cast("CursorResult[object]", result).rowcount or 0
        if deleted:
            log.info("news_digest_purged", deleted=deleted, kept=day.isoformat())
        return deleted


__all__ = ["NewsDigestStore"]
