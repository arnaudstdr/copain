"""Suivi des dépenses : revenus, dépenses ponctuelles, ticks de récurrentes, épargne.

Le module expose :

- `Expense` : modèle SQLAlchemy (table `expenses`, partage la `Base` avec
  `tasks` / `feeds` / `notifications`).
- `ExpenseManager` : CRUD async + agrégations.
- `FinanceConfig` / `RecurringItem` / `extract_finance_config` : lecture
  des dépenses récurrentes depuis `data/profile.yaml`.

Les récurrentes (loyer, abonnements, versements épargne) sont déclarées
dans le YAML. Le pointage (« le loyer est passé ») crée une ligne
`expenses` avec `kind=recurring_tick` ou `saving_tick`.
"""

from __future__ import annotations

from bot.finance.config import (
    EnvelopeItem,
    FinanceConfig,
    RecurringItem,
    RecurringKind,
    extract_finance_config,
)
from bot.finance.manager import ExpenseManager
from bot.finance.models import EXPENSE_KINDS, Expense, ExpenseKind

__all__ = [
    "EXPENSE_KINDS",
    "EnvelopeItem",
    "Expense",
    "ExpenseKind",
    "ExpenseManager",
    "FinanceConfig",
    "RecurringItem",
    "RecurringKind",
    "extract_finance_config",
]
