"""Modèle SQLAlchemy 2.0 pour la table `expenses` (suivi budgétaire).

Une seule table fait tenir 4 types d'écritures différentes (revenu, dépense
ponctuelle, pointage de récurrente, versement épargne) via la colonne
discriminante `kind`. Volume cible ~50 lignes / mois (single user) : aucun
besoin de séparer les concepts en plusieurs tables — le dashboard agrège
tout en 2 requêtes.

Les montants sont stockés en **centimes (int)** pour éviter les arrondis
flottants ; la conversion euros↔cents se fait à la frontière (pipeline +
API). La date est stockée en `Date` (pas `DateTime`) pour éviter qu'une
saisie tardive (23h55 le 31) ne bascule sur le mois suivant en UTC.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal, get_args

from sqlalchemy import Boolean, Date, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from bot.tasks.models import Base

ExpenseKind = Literal["punctual", "recurring_tick", "saving_tick", "income"]
EXPENSE_KINDS: frozenset[str] = frozenset(get_args(ExpenseKind))


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Expense(Base):
    """Écriture budgétaire : revenu, dépense ponctuelle, tick récurrente, épargne."""

    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # Toujours positif. Le signe (entrée vs sortie) se déduit de `kind`.
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)
    # Slug stable de la récurrente (`finances.recurring[].key` côté YAML).
    # Null pour `punctual` et `income`.
    recurring_key: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    # Date FONCTIONNELLE (mois de rattachement). Pas un timestamp.
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # True quand la ligne vient d'un compte joint / hors gestion perso :
    # visible pour le suivi (enveloppe shared) mais exclue du restant
    # prévisionnel et du CSV d'export perso.
    shared: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:
        sign = "+" if self.kind == "income" else "-"
        flag = " shared" if self.shared else ""
        return (
            f"Expense(id={self.id}, {self.kind}, {sign}{self.amount_cents}c, "
            f"{self.label!r}, on={self.occurred_on}{flag})"
        )
