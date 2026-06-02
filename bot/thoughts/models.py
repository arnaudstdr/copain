"""Modèle SQLAlchemy 2.0 pour la table `thoughts` (dépôts cognitifs).

Partage la même `Base` que `tasks` / `feeds` / `notifications` pour rester
dans la base SQLite `tasks.db` (cf. CLAUDE.md, règle "Shared SQLAlchemy
Base"). La migration de schéma se fait via `Base.metadata.create_all`
appelé par `ThoughtManager.init_schema` au boot.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from bot.tasks.models import Base

ThoughtKind = Literal["worry", "idea", "note"]
THOUGHT_KINDS: frozenset[str] = frozenset({"worry", "idea", "note"})


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Thought(Base):
    """Pensée déposée par l'utilisateur, hors flux d'action concrète.

    Distinct de `Task` : une pensée n'a pas vocation à être "faite", elle
    est externalisée pour libérer la mémoire de travail. `kind` permet de
    distinguer une inquiétude récurrente d'une simple note ou d'une idée
    à creuser plus tard.
    """

    __tablename__ = "thoughts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    surfaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        tag = f"[{self.kind}]" if self.kind else "[·]"
        return f"Thought(id={self.id}, {tag} {self.content!r})"
