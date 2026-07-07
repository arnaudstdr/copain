"""Tests du chargement fail-soft de l'état budgétaire (`load_budget_summary`)."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

from bot.finance.budget import BudgetSummary
from bot.finance.config import FinanceConfig, RecurringItem
from bot.finance.summary import load_budget_summary

_CONFIGURED = FinanceConfig(
    currency="EUR",
    recurring=(
        RecurringItem(key="loyer", label="Loyer", amount_cents=50000, day=5, kind="expense"),
    ),
)


def _expenses() -> MagicMock:
    expenses = MagicMock()
    expenses.current_cycle_bounds = AsyncMock(return_value=(date(2026, 7, 1), date(2026, 8, 1)))
    expenses.list_for_cycle = AsyncMock(return_value=[])
    expenses.list_savings_for_year = AsyncMock(return_value=[])
    return expenses


async def test_returns_summary_when_configured() -> None:
    summary = await load_budget_summary(
        expenses=_expenses(), config=_CONFIGURED, timezone="Europe/Paris"
    )
    assert isinstance(summary, BudgetSummary)


async def test_none_when_not_configured_without_touching_sqlite() -> None:
    expenses = _expenses()
    summary = await load_budget_summary(
        expenses=expenses, config=FinanceConfig.empty(), timezone="Europe/Paris"
    )
    assert summary is None
    expenses.current_cycle_bounds.assert_not_awaited()


async def test_none_when_sqlite_fails() -> None:
    expenses = _expenses()
    expenses.list_for_cycle = AsyncMock(side_effect=RuntimeError("db down"))
    summary = await load_budget_summary(
        expenses=expenses, config=_CONFIGURED, timezone="Europe/Paris"
    )
    assert summary is None
