"""Persistance des notifications proactives (cooldowns, budget quotidien, dédup).

La `Base` SQLAlchemy est partagée avec `bot.tasks.models` pour que la table
vive dans le même `tasks.db` et soit créée au même `init_schema()`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from bot.tasks.models import Base, _utcnow


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    event_uid: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"NotificationLog(id={self.id}, kind={self.kind!r}, "
            f"uid={self.event_uid!r}, sent_at={self.sent_at.isoformat()})"
        )
