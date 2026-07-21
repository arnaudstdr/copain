"""CRUD async sur la table `expenses` (revenu, ponctuelle, ticks récurrentes).

L'engine est partagé avec `TaskManager` / `FeedManager` / `ThoughtManager`
via `bot.db.create_shared_engine` — un seul pool sur `tasks.db`.
"""

from __future__ import annotations

import asyncio
import calendar as _calendar
from collections.abc import Sequence
from datetime import date
from typing import Literal

from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from bot.finance.budget import OPEN_CYCLE_END as OPEN_CYCLE_END  # ré-export explicite (mypy strict)
from bot.finance.models import BudgetCycle, Expense
from bot.logging_conf import get_logger
from bot.tasks.models import Base

log = get_logger(__name__)

# `OPEN_CYCLE_END` (borne haute sentinelle d'un cycle ouvert) est défini dans
# `bot.finance.budget` — module pur d'arithmétique de cycle — et ré-exporté ici
# (importé + utilisé plus bas) pour les appelants historiques (`api.py`,
# requêtes `occurred_on < end`).


def _month_bounds(month: date) -> tuple[date, date]:
    """Retourne (1er du mois, 1er du mois suivant) — borne supérieure exclusive."""
    first = month.replace(day=1)
    if first.month == 12:
        nxt = first.replace(year=first.year + 1, month=1)
    else:
        nxt = first.replace(month=first.month + 1)
    return first, nxt


def _year_bounds(year: int) -> tuple[date, date]:
    """Retourne (1er janvier, 1er janvier suivant)."""
    return date(year, 1, 1), date(year + 1, 1, 1)


class ExpenseManager:
    """Wrapper async autour de la table SQLite `expenses`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)
        # Sérialise le check-then-act de `tick_recurring_once` : la fenêtre
        # de cycle est dynamique (ancrée sur les salaires), donc pas de
        # contrainte UNIQUE possible en SQL. Serveur mono-process → un lock
        # asyncio suffit à empêcher un double pointage concurrent.
        self._tick_lock = asyncio.Lock()

    async def init_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await self._migrate_add_shared_column(conn)

    @staticmethod
    async def _migrate_add_shared_column(conn: object) -> None:
        """Ajoute la colonne `shared` sur `expenses` si elle manque (SQLite only).

        `Base.metadata.create_all` ne fait pas d'ADD COLUMN sur table existante.
        Pour les installations qui pré-existent au flag `shared`, on inspecte
        `PRAGMA table_info` et on ALTER TABLE de façon idempotente.
        """
        result = await conn.execute(text("PRAGMA table_info(expenses)"))  # type: ignore[attr-defined]
        cols = {row[1] for row in result.fetchall()}
        if "shared" in cols:
            return
        await conn.execute(  # type: ignore[attr-defined]
            text("ALTER TABLE expenses ADD COLUMN shared BOOLEAN NOT NULL DEFAULT 0")
        )
        log.info("finance_expenses_migrated_add_shared_column")

    async def add_punctual(
        self,
        *,
        amount_cents: int,
        label: str,
        category: str | None,
        occurred_on: date,
        shared: bool = False,
    ) -> Expense:
        """Enregistre une dépense ponctuelle (kind=punctual).

        `shared=True` : dépense réalisée sur un compte joint, exclue du
        restant prévisionnel perso (cf. `compute_budget`).
        """
        expense = Expense(
            kind="punctual",
            amount_cents=amount_cents,
            label=label,
            category=category,
            occurred_on=occurred_on,
            shared=shared,
        )
        return await self._persist(expense)

    async def add_income(
        self,
        *,
        amount_cents: int,
        label: str,
        occurred_on: date,
    ) -> Expense:
        """Enregistre une entrée d'argent (salaire, prime, …)."""
        expense = Expense(
            kind="income",
            amount_cents=amount_cents,
            label=label,
            category=None,
            occurred_on=occurred_on,
        )
        return await self._persist(expense)

    async def tick_recurring(
        self,
        *,
        recurring_key: str,
        label: str,
        amount_cents: int,
        kind: Literal["expense", "saving"],
        occurred_on: date,
        category: str | None = None,
    ) -> Expense:
        """Pointe une récurrente connue.

        `kind` est le type côté YAML : `expense` → `recurring_tick`,
        `saving` → `saving_tick`.
        """
        db_kind = "recurring_tick" if kind == "expense" else "saving_tick"
        expense = Expense(
            kind=db_kind,
            amount_cents=amount_cents,
            label=label,
            recurring_key=recurring_key,
            category=category,
            occurred_on=occurred_on,
        )
        return await self._persist(expense)

    async def tick_recurring_once(
        self,
        *,
        recurring_key: str,
        label: str,
        amount_cents: int,
        kind: Literal["expense", "saving"],
        occurred_on: date,
        category: str | None = None,
    ) -> Expense | None:
        """Pointe une récurrente si elle ne l'est pas déjà dans le cycle.

        Variante atomique de `is_recurring_ticked_in_cycle` + `tick_recurring` :
        le SELECT et l'INSERT sont sérialisés sous un lock pour qu'un double
        envoi concurrent (« le loyer est passé » reçu deux fois) ne produise
        qu'une seule écriture. Retourne `None` si déjà pointée.
        """
        async with self._tick_lock:
            if await self.is_recurring_ticked_in_cycle(recurring_key, occurred_on):
                return None
            return await self.tick_recurring(
                recurring_key=recurring_key,
                label=label,
                amount_cents=amount_cents,
                kind=kind,
                occurred_on=occurred_on,
                category=category,
            )

    async def is_recurring_ticked_this_month(
        self,
        recurring_key: str,
        month: date,
    ) -> bool:
        """`True` si la récurrente a déjà été pointée durant le mois de `month`."""
        start, end = _month_bounds(month)
        async with self._sessionmaker() as session:
            stmt = (
                select(Expense.id)
                .where(
                    and_(
                        Expense.recurring_key == recurring_key,
                        Expense.occurred_on >= start,
                        Expense.occurred_on < end,
                    )
                )
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.first() is not None

    async def list_for_month(self, month: date) -> Sequence[Expense]:
        """Toutes les écritures (kind ∈ tous) rattachées au mois de `month`."""
        start, end = _month_bounds(month)
        async with self._sessionmaker() as session:
            stmt = (
                select(Expense)
                .where(and_(Expense.occurred_on >= start, Expense.occurred_on < end))
                .order_by(Expense.occurred_on.desc(), Expense.id.desc())
            )
            result = await session.execute(stmt)
            return result.scalars().all()  # type: ignore[no-any-return, unused-ignore]

    async def list_between(self, start: date, end: date) -> Sequence[Expense]:
        """Toutes les écritures entre `start` et `end` (bornes incluses).

        Ordre ascendant (du plus ancien au plus récent) : c'est l'ordre
        attendu dans un export tableur, à l'inverse de `list_for_month` qui
        privilégie l'affichage "ce qui vient de se passer en premier".
        """
        async with self._sessionmaker() as session:
            stmt = (
                select(Expense)
                .where(and_(Expense.occurred_on >= start, Expense.occurred_on <= end))
                .order_by(Expense.occurred_on.asc(), Expense.id.asc())
            )
            result = await session.execute(stmt)
            return result.scalars().all()  # type: ignore[no-any-return, unused-ignore]

    async def list_savings_for_year(self, year: int) -> Sequence[Expense]:
        """Tous les ticks d'épargne (kind=saving_tick) de l'année."""
        start, end = _year_bounds(year)
        async with self._sessionmaker() as session:
            stmt = (
                select(Expense)
                .where(
                    and_(
                        Expense.kind == "saving_tick",
                        Expense.occurred_on >= start,
                        Expense.occurred_on < end,
                    )
                )
                .order_by(Expense.occurred_on.desc(), Expense.id.desc())
            )
            result = await session.execute(stmt)
            return result.scalars().all()  # type: ignore[no-any-return, unused-ignore]

    # ------------------------------------------------------------------ #
    # Cycles budgétaires (ancrés sur la date de perception du salaire)
    # ------------------------------------------------------------------ #

    async def start_cycle(self, started_on: date) -> BudgetCycle:
        """Démarre un cycle budgétaire à `started_on` (jour du salaire reçu).

        Idempotent : si une ancre existe déjà pour cette date, on la
        retourne sans en créer une seconde (le LLM + le cron peuvent
        rejouer le même « salaire reçu »).
        """
        async with self._sessionmaker() as session:
            existing = await session.execute(
                select(BudgetCycle).where(BudgetCycle.started_on == started_on).limit(1)
            )
            found: BudgetCycle | None = existing.scalars().first()
            if found is not None:
                return found
            cycle = BudgetCycle(started_on=started_on)
            session.add(cycle)
            await session.commit()
            await session.refresh(cycle)
            return cycle

    async def _cycle_starts(self) -> list[date]:
        """Toutes les dates d'ancrage de cycle, ordre croissant."""
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(BudgetCycle.started_on).order_by(BudgetCycle.started_on.asc())
            )
            return list(result.scalars().all())

    async def current_cycle_bounds(self, today: date) -> tuple[date, date]:
        """Retourne (début inclus, fin exclue) du cycle budgétaire courant.

        Le cycle courant commence à la dernière ancre <= `today` et finit à
        l'ancre suivante (exclue) ou reste ouvert (`OPEN_CYCLE_END`).

        Tant qu'aucune ancre n'existe (ou que `today` précède la première),
        on retombe sur le mois civil — comportement historique préservé pour
        les installations qui n'ont jamais déclaré de salaire.
        """
        starts = await self._cycle_starts()
        priors = [s for s in starts if s <= today]
        if not priors:
            return _month_bounds(today)
        start = priors[-1]
        laters = [s for s in starts if s > start]
        end = laters[0] if laters else OPEN_CYCLE_END
        return start, end

    async def list_for_cycle(self, today: date) -> Sequence[Expense]:
        """Toutes les écritures rattachées au cycle budgétaire courant."""
        start, end = await self.current_cycle_bounds(today)
        async with self._sessionmaker() as session:
            stmt = (
                select(Expense)
                .where(and_(Expense.occurred_on >= start, Expense.occurred_on < end))
                .order_by(Expense.occurred_on.desc(), Expense.id.desc())
            )
            result = await session.execute(stmt)
            return result.scalars().all()  # type: ignore[no-any-return, unused-ignore]

    async def is_recurring_ticked_in_cycle(self, recurring_key: str, today: date) -> bool:
        """`True` si la récurrente a déjà été pointée dans le cycle courant."""
        start, end = await self.current_cycle_bounds(today)
        async with self._sessionmaker() as session:
            stmt = (
                select(Expense.id)
                .where(
                    and_(
                        Expense.recurring_key == recurring_key,
                        Expense.occurred_on >= start,
                        Expense.occurred_on < end,
                    )
                )
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.first() is not None

    async def update(
        self,
        expense_id: int,
        *,
        amount_cents: int | None = None,
        label: str | None = None,
        category: str | None = None,
        occurred_on: date | None = None,
        shared: bool | None = None,
    ) -> Expense | None:
        """Met à jour partiellement une écriture (correction de saisie).

        Sémantique PATCH : seuls les champs non-`None` sont écrits. `kind` et
        `recurring_key` ne sont pas éditables (corriger un kind = supprimer +
        recréer). Retourne l'`Expense` mise à jour, ou `None` si l'id est
        inconnu.
        """
        async with self._sessionmaker() as session:
            # Annotation explicite : le mypy pre-commit (contexte limité aux
            # fichiers modifiés) voit `session.get` comme `Any` et refuse le
            # `return expense` final (no-any-return). Cf. mémoire projet.
            expense: Expense | None = await session.get(Expense, expense_id)
            if expense is None:
                return None
            if amount_cents is not None:
                expense.amount_cents = amount_cents
            if label is not None:
                expense.label = label
            if category is not None:
                expense.category = category
            if occurred_on is not None:
                expense.occurred_on = occurred_on
            if shared is not None:
                expense.shared = shared
            await session.commit()
            await session.refresh(expense)
            return expense

    async def delete(self, expense_id: int) -> bool:
        """Supprime une écriture. Retourne `False` si l'id est inconnu.

        Supprimer un `recurring_tick` « dépointe » la récurrente : elle
        redevient pending au prochain `compute_budget` (aucune trace ne reste
        dans le cycle). Supprimer un `income` ne touche pas l'ancre de cycle
        (`budget_cycles`, table séparée).
        """
        async with self._sessionmaker() as session:
            expense = await session.get(Expense, expense_id)
            if expense is None:
                return False
            await session.delete(expense)
            await session.commit()
        return True

    async def _persist(self, expense: Expense) -> Expense:
        async with self._sessionmaker() as session:
            session.add(expense)
            await session.commit()
            await session.refresh(expense)
        return expense


def clamp_day_to_month(day: int, month: date) -> int:
    """Cap `day` au dernier jour du mois (utile pour day=31 en février)."""
    last = _calendar.monthrange(month.year, month.month)[1]
    return min(day, last)
