"""Calcul du restant prévisionnel du mois + agrégat épargne annuelle.

Pure functions, sans I/O. Entrées : la config YAML (récurrentes), les
écritures SQL du mois courant, les ticks d'épargne de l'année. Sortie :
un `BudgetSummary` consommable directement par le dashboard.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from bot.finance.config import EnvelopeItem, FinanceConfig, RecurringItem, RecurringKind
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
class EnvelopeStatus:
    """État courant d'une enveloppe budgétaire mensuelle."""

    category: str
    label: str
    allocated_cents: int
    spent_cents: int  # somme des ponctuelles avec cette catégorie ce mois
    overrun_cents: int  # max(0, spent - allocated)

    @property
    def remaining_cents(self) -> int:
        """Reste dans l'enveloppe (peut être négatif en cas de dépassement)."""
        return self.allocated_cents - self.spent_cents

    @property
    def is_overrun(self) -> bool:
        return self.spent_cents > self.allocated_cents


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
    envelopes: tuple[EnvelopeStatus, ...] = ()

    @property
    def pending_total_cents(self) -> int:
        return sum(p.amount_cents for p in self.pending_recurring)

    @property
    def envelopes_allocated_cents(self) -> int:
        return sum(e.allocated_cents for e in self.envelopes)

    @property
    def envelopes_spent_in_cents(self) -> int:
        """Ce qui a déjà été consommé dans les enveloppes (capé à l'allocation)."""
        return sum(min(e.spent_cents, e.allocated_cents) for e in self.envelopes)

    @property
    def envelopes_overrun_cents(self) -> int:
        return sum(e.overrun_cents for e in self.envelopes)

    @property
    def remaining_cents(self) -> int:
        """Restant previsionnel.

        Les ponctuelles déjà passées sous une enveloppe NE comptent PAS une
        deuxième fois (elles puisent dans l'enveloppe, pas dans le restant).
        En revanche, le débordement (overrun) vient bien grignoter le
        restant — sinon on mentirait à l'utilisateur.

        = revenu
          - punctual_hors_enveloppes
          - recurring_tick
          - saving_tick
          - pending récurrentes
          - allocated total des enveloppes
          - overrun total
        """
        # Tout l'argent puisé dans les enveloppes (cumul réel des ponctuelles
        # matchées par catégorie). Ce montant a déjà été "soustrait" via
        # l'allocation + l'overrun ; on l'enlève de spent_punctual_cents pour
        # ne pas le compter deux fois.
        punctual_in_envelopes = sum(e.spent_cents for e in self.envelopes)
        punctual_hors_envelopes = self.spent_punctual_cents - punctual_in_envelopes
        out = (
            punctual_hors_envelopes
            + self.spent_recurring_cents
            + self.saved_this_month_cents
            + self.pending_total_cents
            + self.envelopes_allocated_cents
            + self.envelopes_overrun_cents
        )
        return self.income_cents - out

    @property
    def pending_recurring_count(self) -> int:
        return len(self.pending_recurring)

    @property
    def has_overdue(self) -> bool:
        return any(p.is_overdue for p in self.pending_recurring)

    @property
    def has_envelope_overrun(self) -> bool:
        return any(e.is_overrun for e in self.envelopes)


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
    envelopes = tuple(_envelopes_status(config.envelopes, month_expenses))

    saved_this_year = sum(e.amount_cents for e in year_savings if e.kind == "saving_tick")

    return BudgetSummary(
        month=month_start,
        income_cents=income_cents,
        spent_punctual_cents=spent_punctual,
        spent_recurring_cents=spent_recurring,
        saved_this_month_cents=saved_this_month,
        pending_recurring=pending,
        saved_this_year_cents=saved_this_year,
        envelopes=envelopes,
    )


def _envelopes_status(
    envelopes: Sequence[EnvelopeItem],
    month_expenses: Sequence[Expense],
) -> list[EnvelopeStatus]:
    """Pour chaque enveloppe, calcule le montant consommé par les ponctuelles.

    Le matching `category` est insensible à la casse et aux espaces, pour
    encaisser les variations du LLM ("Essence" vs "essence").
    """
    if not envelopes:
        return []
    spent_by_category: dict[str, int] = {}
    for e in month_expenses:
        if e.kind != "punctual" or not e.category:
            continue
        key = e.category.strip().lower()
        spent_by_category[key] = spent_by_category.get(key, 0) + e.amount_cents

    out: list[EnvelopeStatus] = []
    for env in envelopes:
        key = env.category.strip().lower()
        spent = spent_by_category.get(key, 0)
        overrun = max(0, spent - env.amount_cents)
        out.append(
            EnvelopeStatus(
                category=env.category,
                label=env.label,
                allocated_cents=env.amount_cents,
                spent_cents=spent,
                overrun_cents=overrun,
            )
        )
    return out


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
