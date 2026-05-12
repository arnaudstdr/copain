"""Modèle SQLAlchemy pour l'historique des events de localisation iOS.

Cette table reçoit les events arrived/left envoyés par les automations
iOS Shortcuts à chaque entrée/sortie d'une géofence (maison, bureau).
La `Base` est partagée avec `bot.tasks.models` pour que la table soit
créée dans `tasks.db` au même `init_schema()`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from bot.tasks.models import Base, _utcnow


class LocationEvent(Base):
    __tablename__ = "location_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # "arrived" ou "left" — on garde un String plutôt qu'un Enum SQLAlchemy
    # pour rester souple et tolérer de futurs types (ex: "passing").
    event_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # Label libre côté iOS ("home", "work", autre). On ne contraint pas
    # la valeur en base — la validation se fait à l'entrée HTTP.
    place: Mapped[str] = mapped_column(String, nullable=False, index=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Timestamp envoyé par iOS (ou par défaut now). Différent de created_at
    # qui est le moment de réception côté serveur (peut différer si l'iPhone
    # était hors ligne au moment de la transition).
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        return (
            f"LocationEvent(id={self.id}, {self.event_type} {self.place!r} "
            f"at {self.occurred_at.isoformat()})"
        )
