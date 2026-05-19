"""Calcul du restant prévisionnel du mois + agrégat épargne annuelle.

Pure functions, sans I/O. Entrées : la config YAML (récurrentes), les
écritures SQL du mois courant, les ticks d'épargne de l'année. Sortie :
un `BudgetSummary` consommable directement par le dashboard.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from bot.finance.config import FinanceConfig, RecurringItem, RecurringKind
from bot.finance.manager import clamp_day_to_month
from bot.finance.models import Expense


@dataclass(frozen=True, slots=True)
class PendingRecurring:
    """Récurrente du YAML pas encore pointée sur le mois courant."""

    key: str
    label: str
    amount_cents: int
    day: int  # jour effectif (après cap au dernier jour du mois)
    kind: RecurringKind
    is_overdue: bool  # day < today.day et toujours pas pointée


@dataclass(frozen=True, slots=True)
class BudgetSummary:
    """État agrégé exposé par la card Budget du dashboard."""

    month: date  # 1er du mois
    income_cents: int
    spent_punctual_cents: int
    spent_recurring_cents: int  # somme des kind=recurring_tick du mois
    saved_this_month_cents: int  # somme des kind=saving_tick du mois
    pending_recurring: tuple[PendingRecurring, ...]
    saved_this_year_cents: int  # cumul kind=saving_tick depuis le 1er janvier

    @property
    def pending_total_cents(self) -> int:
        return sum(p.amount_cents for p in self.pending_recurring)

    @property
    def remaining_cents(self) -> int:
        """Restant previsionnel = revenu - tout ce qui sort (reel + pending)."""
        out = (
            self.spent_punctual_cents
            + self.spent_recurring_cents
            + self.saved_this_month_cents
            + self.pending_total_cents
        )
        return self.income_cents - out

    @property
    def pending_recurring_count(self) -> int:
        return len(self.pending_recurring)

    @property
    def has_overdue(self) -> bool:
        return any(p.is_overdue for p in self.pending_recurring)


def compute_budget(
    *,
    config: FinanceConfig,
    month_expenses: Sequence[Expense],
    year_savings: Sequence[Expense],
    today: date,
) -> BudgetSummary:
    """Compose un `BudgetSummary` à partir des sources de données."""
    month_start = today.replace(day=1)

    income_cents = sum(e.amount_cents for e in month_expenses if e.kind == "income")
    spent_punctual = sum(e.amount_cents for e in month_expenses if e.kind == "punctual")
    spent_recurring = sum(e.amount_cents for e in month_expenses if e.kind == "recurring_tick")
    saved_this_month = sum(e.amount_cents for e in month_expenses if e.kind == "saving_tick")

    ticked_keys = {
        e.recurring_key
        for e in month_expenses
        if e.kind in {"recurring_tick", "saving_tick"} and e.recurring_key is not None
    }

    pending = tuple(_pending_for_month(config.recurring, ticked_keys, today))

    saved_this_year = sum(e.amount_cents for e in year_savings if e.kind == "saving_tick")

    return BudgetSummary(
        month=month_start,
        income_cents=income_cents,
        spent_punctual_cents=spent_punctual,
        spent_recurring_cents=spent_recurring,
        saved_this_month_cents=saved_this_month,
        pending_recurring=pending,
        saved_this_year_cents=saved_this_year,
    )


def _pending_for_month(
    recurring: Sequence[RecurringItem],
    ticked_keys: set[str],
    today: date,
) -> list[PendingRecurring]:
    pending: list[PendingRecurring] = []
    for item in recurring:
        if item.key in ticked_keys:
            continue
        effective_day = clamp_day_to_month(item.day, today)
        pending.append(
            PendingRecurring(
                key=item.key,
                label=item.label,
                amount_cents=item.amount_cents,
                day=effective_day,
                kind=item.kind,
                is_overdue=effective_day < today.day,
            )
        )
    return pending
