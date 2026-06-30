"""CRUD async sur la table `thoughts` via SQLAlchemy + aiosqlite."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from bot.logging_conf import get_logger
from bot.tasks.models import Base
from bot.thoughts.models import THOUGHT_KINDS, Thought

log = get_logger(__name__)


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
            # Micro-migration idempotente : `create_all` n'ajoute jamais de
            # colonne à une table existante, donc une base créée avant
            # l'introduction de `surfaced_at` ne l'a pas. On vérifie via
            # PRAGMA et on ALTER si besoin (pattern réutilisable pour toute
            # future colonne ajoutée à une table déjà déployée).
            result = await conn.execute(text("PRAGMA table_info(thoughts)"))
            columns = {row[1] for row in result.fetchall()}
            if "surfaced_at" not in columns:
                await conn.execute(text("ALTER TABLE thoughts ADD COLUMN surfaced_at DATETIME"))
                log.info("thoughts_schema_migrated", added_column="surfaced_at")

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

    async def list_recent(self, limit: int = 50, kind: str | None = None) -> Sequence[Thought]:
        """Retourne les dépôts les plus récents (ordre chronologique inverse).

        `kind` filtre strictement par type (`worry|idea|note`) quand fourni ;
        les dépôts d'un autre type — ou au `kind` null — sont exclus.
        """
        async with self._sessionmaker() as session:
            stmt = select(Thought)
            if kind is not None:
                stmt = stmt.where(Thought.kind == kind)
            stmt = stmt.order_by(Thought.created_at.desc()).limit(limit)
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

    async def close(self, thought_id: int) -> bool:
        """Clôt un dépôt (écrit `processed_at`). Idempotent.

        Retourne `True` si le dépôt existe (qu'il vienne d'être clos ou
        qu'il l'ait déjà été — le `processed_at` initial n'est jamais
        écrasé), `False` si l'id est inconnu.
        """
        async with self._sessionmaker() as session:
            thought = await session.get(Thought, thought_id)
            if thought is None:
                return False
            if thought.processed_at is None:
                thought.processed_at = datetime.now(UTC)
                await session.commit()
                log.info("thought_closed", thought_id=thought_id)
            return True

    async def list_open(
        self, kinds: Sequence[str] | None = None, limit: int = 20
    ) -> Sequence[Thought]:
        """Retourne les dépôts ouverts (`processed_at IS NULL`), récents d'abord.

        `kinds` filtre strictement : un dépôt au `kind` null est exclu dès
        qu'un filtre est fourni. Le défaut `limit=20` borne la volumétrie
        évaluée par les heuristiques de restitution.
        """
        async with self._sessionmaker() as session:
            stmt = select(Thought).where(Thought.processed_at.is_(None))
            if kinds is not None:
                stmt = stmt.where(Thought.kind.in_(kinds))
            stmt = stmt.order_by(Thought.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            return result.scalars().all()  # type: ignore[no-any-return, unused-ignore]

    async def mark_surfaced(self, ids: Sequence[int]) -> None:
        """Tamponne `surfaced_at = utcnow()` sur les dépôts ciblés (no-op si vide)."""
        if not ids:
            return
        async with self._sessionmaker() as session:
            stmt = update(Thought).where(Thought.id.in_(ids)).values(surfaced_at=datetime.now(UTC))
            await session.execute(stmt)
            await session.commit()
