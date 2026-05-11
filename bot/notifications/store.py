"""Store async des notifications poussées (briefing, rappels de tâches, proactivité).

Les jobs APScheduler écrivent dans cette table, et `GET /notifications`
les lit puis les marque comme lues. Le client iOS (raccourci via Tailscale)
poll cet endpoint régulièrement.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, update
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
