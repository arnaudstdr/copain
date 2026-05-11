"""Modèle SQLAlchemy pour la file des notifications poussées (briefing, rappels, proactivité).

La `Base` est partagée avec `bot.tasks.models` pour que la table vive dans le
même `tasks.db` et soit créée au même `init_schema()`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from bot.tasks.models import Base, _utcnow


class PendingNotification(Base):
    __tablename__ = "pending_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    def __repr__(self) -> str:
        state = "read" if self.read_at is not None else "unread"
        return (
            f"PendingNotification(id={self.id}, {state}, created_at={self.created_at.isoformat()})"
        )
