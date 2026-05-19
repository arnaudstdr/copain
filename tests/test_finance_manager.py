"""Tests de l'`ExpenseManager` sur une base SQLite temporaire."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from bot.db import create_shared_engine
from bot.finance.manager import ExpenseManager, clamp_day_to_month


@pytest.fixture
async def manager(tmp_data_dir: Path) -> ExpenseManager:
    engine = create_shared_engine(tmp_data_dir / "tasks.db")
    mgr = ExpenseManager(engine)
    await mgr.init_schema()
    yield mgr
    await engine.dispose()


async def test_add_punctual_persists_with_kind(manager: ExpenseManager) -> None:
    expense = await manager.add_punctual(
        amount_cents=2700,
        label="Pharmacie",
        category="santé",
        occurred_on=date(2026, 5, 18),
    )
    assert expense.id is not None
    assert expense.kind == "punctual"
    assert expense.amount_cents == 2700
    assert expense.label == "Pharmacie"
    assert expense.category == "santé"
    assert expense.recurring_key is None


async def test_add_income_kind_income(manager: ExpenseManager) -> None:
    expense = await manager.add_income(
        amount_cents=250000,
        label="Salaire mai",
        occurred_on=date(2026, 5, 5),
    )
    assert expense.kind == "income"
    assert expense.amount_cents == 250000


async def test_tick_recurring_expense_uses_recurring_tick_kind(
    manager: ExpenseManager,
) -> None:
    tick = await manager.tick_recurring(
        recurring_key="loyer",
        label="Loyer appartement",
        amount_cents=80000,
        kind="expense",
        occurred_on=date(2026, 5, 5),
    )
    assert tick.kind == "recurring_tick"
    assert tick.recurring_key == "loyer"


async def test_tick_recurring_saving_uses_saving_tick_kind(
    manager: ExpenseManager,
) -> None:
    tick = await manager.tick_recurring(
        recurring_key="pel",
        label="Versement PEL",
        amount_cents=20000,
        kind="saving",
        occurred_on=date(2026, 5, 5),
    )
    assert tick.kind == "saving_tick"
    assert tick.recurring_key == "pel"


async def test_is_recurring_ticked_this_month_true_after_tick(
    manager: ExpenseManager,
) -> None:
    await manager.tick_recurring(
        recurring_key="loyer",
        label="Loyer",
        amount_cents=80000,
        kind="expense",
        occurred_on=date(2026, 5, 5),
    )
    assert await manager.is_recurring_ticked_this_month("loyer", date(2026, 5, 18))


async def test_is_recurring_ticked_this_month_false_when_no_tick(
    manager: ExpenseManager,
) -> None:
    assert not await manager.is_recurring_ticked_this_month("loyer", date(2026, 5, 18))


async def test_is_recurring_ticked_isolates_months(manager: ExpenseManager) -> None:
    await manager.tick_recurring(
        recurring_key="loyer",
        label="Loyer",
        amount_cents=80000,
        kind="expense",
        occurred_on=date(2026, 4, 5),
    )
    assert await manager.is_recurring_ticked_this_month("loyer", date(2026, 4, 18))
    assert not await manager.is_recurring_ticked_this_month("loyer", date(2026, 5, 18))


async def test_list_for_month_filters_by_window(manager: ExpenseManager) -> None:
    await manager.add_income(amount_cents=250000, label="Salaire mai", occurred_on=date(2026, 5, 5))
    await manager.add_punctual(
        amount_cents=2700, label="Pharmacie", category=None, occurred_on=date(2026, 5, 10)
    )
    await manager.add_punctual(
        amount_cents=1500, label="Train avril", category=None, occurred_on=date(2026, 4, 28)
    )
    rows = await manager.list_for_month(date(2026, 5, 1))
    assert len(rows) == 2
    labels = {r.label for r in rows}
    assert labels == {"Salaire mai", "Pharmacie"}


async def test_list_savings_for_year_only_returns_saving_tick(
    manager: ExpenseManager,
) -> None:
    await manager.tick_recurring(
        recurring_key="pel",
        label="PEL",
        amount_cents=20000,
        kind="saving",
        occurred_on=date(2026, 1, 5),
    )
    await manager.tick_recurring(
        recurring_key="pel",
        label="PEL",
        amount_cents=20000,
        kind="saving",
        occurred_on=date(2026, 3, 5),
    )
    # Pollue avec une récurrente classique (ne doit pas remonter)
    await manager.tick_recurring(
        recurring_key="loyer",
        label="Loyer",
        amount_cents=80000,
        kind="expense",
        occurred_on=date(2026, 2, 5),
    )
    # Et une vieille épargne d'une autre année
    await manager.tick_recurring(
        recurring_key="pel",
        label="PEL",
        amount_cents=20000,
        kind="saving",
        occurred_on=date(2025, 12, 5),
    )

    rows = await manager.list_savings_for_year(2026)
    assert len(rows) == 2
    assert all(r.kind == "saving_tick" for r in rows)


def test_clamp_day_to_month_caps_february() -> None:
    assert clamp_day_to_month(31, date(2026, 2, 1)) == 28
    assert clamp_day_to_month(31, date(2024, 2, 1)) == 29  # année bissextile
    assert clamp_day_to_month(31, date(2026, 4, 1)) == 30
    assert clamp_day_to_month(15, date(2026, 5, 1)) == 15
