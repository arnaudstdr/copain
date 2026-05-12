"""Store async des notifications poussées (briefing, rappels de tâches, proactivité).

Les jobs APScheduler écrivent dans cette table, et `GET /notifications`
les lit puis les marque comme lues. Le client iOS (raccourci via Tailscale)
poll cet endpoint régulièrement.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from bot.logging_conf import get_logger
from bot.notifications.models import PendingNotification
from bot.tasks.models import Base

if TYPE_CHECKING:
    from bot.notifications.pushover import PushoverClient

log = get_logger(__name__)


class NotificationStore:
    """CRUD minimal sur la file `pending_notifications`."""

    def __init__(
        self,
        engine: AsyncEngine,
        pushover: PushoverClient | None = None,
    ) -> None:
        self._engine = engine
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)
        self._pushover = pushover

    async def init_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def add(
        self,
        text: str,
        title: str = "Copain",
        priority: int = 0,
        sound: str | None = None,
    ) -> None:
        """Empile une notification (non lue) et la pousse via Pushover si configuré."""
        async with self._sessionmaker() as session:
            session.add(PendingNotification(text=text))
            await session.commit()
        log.info("notification_enqueued", chars=len(text))
        if self._pushover is not None:
            await self._pushover.push(text, title=title, priority=priority, sound=sound)

    async def get_unread(self) -> Sequence[PendingNotification]:
        """Liste les notifications non encore consommées, plus anciennes en tête."""
        async with self._sessionmaker() as session:
            stmt = (
                select(PendingNotification)
                .where(PendingNotification.read_at.is_(None))
                .order_by(PendingNotification.created_at)
            )
            result = await session.execute(stmt)
            return result.scalars().all()  # type: ignore[no-any-return, unused-ignore]

    async def mark_read(self, ids: list[int]) -> None:
        """Marque les notifications comme lues (sans les supprimer, utile pour l'audit)."""
        if not ids:
            return
        now = datetime.now(UTC)
        async with self._sessionmaker() as session:
            stmt = (
                update(PendingNotification)
                .where(PendingNotification.id.in_(ids))
                .values(read_at=now)
            )
            await session.execute(stmt)
            await session.commit()
        log.info("notifications_marked_read", count=len(ids))

    async def count_unread(self) -> int:
        """Compte les notifications non lues sans muter la file.

        Utilisé par `GET /dashboard` qui doit pouvoir être appelé à volonté.
        Contrairement à `get_unread()` suivi de `mark_read()`, aucun
        `read_at` n'est posé.
        """
        async with self._sessionmaker() as session:
            stmt = (
                select(func.count())
                .select_from(PendingNotification)
                .where(PendingNotification.read_at.is_(None))
            )
            result = await session.execute(stmt)
            return int(result.scalar_one())

    async def latest_with_text_prefix(
        self,
        prefix: str,
        since: datetime,
    ) -> PendingNotification | None:
        """Retourne la notification la plus récente dont le texte commence par `prefix`.

        Filtré sur `created_at >= since` pour ne pas remonter d'historique.
        Sert au dashboard à exposer le dernier briefing du jour (le modèle
        `PendingNotification` n'a pas de colonne `title` ou `category` ;
        on filtre sur le préfixe du texte, ce qui est suffisant pour les
        catégories de notif aux préfixes stables comme le briefing matinal).
        """
        async with self._sessionmaker() as session:
            stmt = (
                select(PendingNotification)
                .where(PendingNotification.text.startswith(prefix))
                .where(PendingNotification.created_at >= since)
                .order_by(PendingNotification.created_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()  # type: ignore[no-any-return, unused-ignore]
