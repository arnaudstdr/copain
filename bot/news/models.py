"""Modèle SQLAlchemy 2.0 pour la table `news_digests` (digest actu du jour).

Partage la même `Base` que `tasks` / `feeds` / `thoughts` / `chat_messages`
pour rester dans la base SQLite `tasks.db` (cf. CLAUDE.md, règle "Shared
SQLAlchemy Base"). La création de schéma se fait via
`Base.metadata.create_all` appelé par `NewsDigestStore.init_schema` au boot.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from bot.tasks.models import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class NewsDigest(Base):
    """Le digest actu d'une journée civile, resservi à chaque tap du jour.

    La table ne contient jamais plus d'une ligne : `NewsDigestStore.save`
    remplace tout (cf. SPEC décision 2 — un seul digest conservé, celui du
    jour en cours). `digest_date` est la date civile au fuseau
    `settings.timezone`, sérialisée en ISO `YYYY-MM-DD`.
    """

    __tablename__ = "news_digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    digest_date: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    markdown: Mapped[str] = mapped_column(String, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return f"NewsDigest(digest_date={self.digest_date!r}, {len(self.markdown)} chars)"
