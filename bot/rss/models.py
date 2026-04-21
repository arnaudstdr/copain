"""Modèle SQLAlchemy pour la table des flux RSS.

Partage la même `Base` (`DeclarativeBase`) que `bot/tasks/models.py` pour que
`Base.metadata.create_all` crée aussi la table `feeds` dans `tasks.db`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from bot.tasks.models import Base, _utcnow


class Feed(Base):
    __tablename__ = "feeds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    category: Mapped[str] = mapped_column(String, nullable=False, default="general")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        state = "on" if self.enabled else "off"
        return f"Feed(id={self.id}, {state} {self.name!r} [{self.category}], {self.url})"
