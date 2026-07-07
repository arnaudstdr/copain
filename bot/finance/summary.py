"""Chargement fail-soft de l'état budgétaire courant.

Encapsule le trio d'appels async à `ExpenseManager` + `compute_budget`
(pur) répété dans `dashboard.py` et `pipeline/side_effects.py`. Isolé ici
pour être réutilisable sans dépendre de `BotDeps` (la card « Pour toi »
croise un souci d'argent avec ce résumé). Toute panne (YAML mal formé,
SQLite indisponible) est avalée → `None`.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from bot.finance.budget import compute_budget
from bot.logging_conf import get_logger

if TYPE_CHECKING:
    from bot.finance.budget import BudgetSummary
    from bot.finance.config import FinanceConfig
    from bot.finance.manager import ExpenseManager

log = get_logger(__name__)


async def load_budget_summary(
    *,
    expenses: ExpenseManager,
    config: FinanceConfig,
    timezone: str,
) -> BudgetSummary | None:
    """Résumé budgétaire du cycle courant, ou `None` (non configuré / panne).

    Fail-soft : ne lève jamais. `config.is_configured` faux → `None` sans
    toucher SQLite.
    """
    if not config.is_configured:
        return None
    try:
        today_d = datetime.now(ZoneInfo(timezone)).date()
        cycle_start, cycle_end = await expenses.current_cycle_bounds(today_d)
        cycle_rows = await expenses.list_for_cycle(today_d)
        year_savings = await expenses.list_savings_for_year(today_d.year)
        return compute_budget(
            config=config,
            month_expenses=cycle_rows,
            year_savings=year_savings,
            today=today_d,
            cycle_start=cycle_start,
            cycle_end=cycle_end,
        )
    except Exception as exc:
        log.warning("budget_summary_skipped", error=str(exc))
        return None
