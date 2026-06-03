"""CRUD async sur la table `chat_messages` via SQLAlchemy + aiosqlite."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from bot.chat.models import CHAT_ROLES, ChatMessage
from bot.logging_conf import get_logger
from bot.tasks.models import Base

log = get_logger(__name__)


@dataclass
class ChatHistoryPage:
    """Une page d'historique pour le scroll infini de la PWA.

    `messages` est en ordre chronologique croissant (du plus ancien au plus
    récent) pour un affichage direct. `has_more` indique qu'il existe des
    bulles encore plus anciennes (curseur `before_id` à passer pour la page
    suivante = `messages[0].id`).
    """

    messages: Sequence[ChatMessage]
    has_more: bool


class ChatHistoryManager:
    """Wrapper async autour de la table SQLite `chat_messages`.

    L'engine est partagé avec `TaskManager` / `ThoughtManager` / … (cf.
    `bot.db.create_shared_engine`). Aucun scheduler rattaché : on n'enregistre
    que les échanges du mode dialogue pour réafficher les bulles, jamais pour
    les renvoyer spontanément à l'utilisateur.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)

    async def init_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def add_exchange(self, user_text: str, assistant_text: str) -> None:
        """Persiste un échange (bulle user puis bulle assistant) dans l'ordre.

        Insérées dans la même transaction : les id restent contigus et
        ordonnés (user < assistant), ce qui garantit l'ordre d'affichage.
        """
        async with self._sessionmaker() as session:
            session.add(ChatMessage(role="user", content=user_text))
            session.add(ChatMessage(role="assistant", content=assistant_text))
            await session.commit()

    async def page(self, limit: int = 50, before_id: int | None = None) -> ChatHistoryPage:
        """Retourne les `limit` derniers messages (avant `before_id` si fourni).

        Sélection descendante (les plus récents d'abord) avec un élément
        sentinelle en plus pour détecter `has_more`, puis réordonnée en
        chronologique croissant pour l'affichage.
        """
        capped = max(1, min(limit, 200))
        async with self._sessionmaker() as session:
            stmt = select(ChatMessage)
            if before_id is not None:
                stmt = stmt.where(ChatMessage.id < before_id)
            stmt = stmt.order_by(ChatMessage.id.desc()).limit(capped + 1)
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
        has_more = len(rows) > capped
        rows = rows[:capped]
        rows.reverse()  # chronologique croissant
        return ChatHistoryPage(messages=rows, has_more=has_more)

    async def purge_older_than(self, days: int) -> int:
        """Supprime les bulles plus vieilles que `days` (fenêtre glissante).

        Retourne le nombre de lignes supprimées. `days <= 0` est un no-op
        (garde-fou : on ne vide jamais toute la table par erreur de config).
        """
        if days <= 0:
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=days)
        async with self._sessionmaker() as session:
            result = await session.execute(
                delete(ChatMessage).where(ChatMessage.created_at < cutoff)
            )
            await session.commit()
        deleted = cast("CursorResult[object]", result).rowcount or 0
        if deleted:
            log.info("chat_history_purged", deleted=deleted, older_than_days=days)
        return deleted


__all__ = ["CHAT_ROLES", "ChatHistoryManager", "ChatHistoryPage"]
