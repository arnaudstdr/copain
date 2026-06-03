"""Modèle SQLAlchemy 2.0 pour la table `chat_messages` (historique dialogue).

Partage la même `Base` que `tasks` / `feeds` / `thoughts` pour rester dans la
base SQLite `tasks.db` (cf. CLAUDE.md, règle "Shared SQLAlchemy Base"). La
création de schéma se fait via `Base.metadata.create_all` appelé par
`ChatHistoryManager.init_schema` au boot.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from bot.tasks.models import Base

ChatRole = Literal["user", "assistant"]
CHAT_ROLES: frozenset[str] = frozenset({"user", "assistant"})


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ChatMessage(Base):
    """Une bulle affichée dans le mode dialogue de la PWA.

    Persistée uniquement pour le chemin streamé (`/ask/stream`) : Siri, les
    photos et la bulle éphémère du dashboard passent par `process_message`
    (non streamé) et ne sont volontairement pas historisés ici.

    L'ordre d'affichage s'appuie sur `id` (autoincrément, monotone = ordre
    d'insertion réel) plutôt que sur `created_at` : les deux bulles d'un même
    échange peuvent partager le même timestamp à la microseconde près, mais
    leurs id restent strictement ordonnés (user inséré avant assistant).
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )

    def __repr__(self) -> str:
        return f"ChatMessage(id={self.id}, {self.role}: {self.content[:40]!r})"
