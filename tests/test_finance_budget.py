"""Tests purs de `compute_budget` (pas de SQLite, pas d'I/O)."""

from __future__ import annotations

from datetime import date

from bot.finance.budget import compute_budget
from bot.finance.config import FinanceConfig, RecurringItem
from bot.finance.models import Expense


def _config(*items: RecurringItem) -> FinanceConfig:
    return FinanceConfig(currency="EUR", recurring=items)


def _income(cents: int, day: int = 5, month: int = 5) -> Expense:
    return Expense(
        kind="income",
        amount_cents=cents,
        label="Salaire",
        occurred_on=date(2026, month, day),
    )


def _punctual(cents: int, day: int = 10) -> Expense:
    return Expense(
        kind="punctual",
        amount_cents=cents,
        label="Achat",
        occurred_on=date(2026, 5, day),
    )


def _tick(key: str, cents: int, day: int = 5, kind: str = "recurring_tick") -> Expense:
    return Expense(
        kind=kind,
        amount_cents=cents,
        label=key,
        recurring_key=key,
        occurred_on=date(2026, 5, day),
    )


def test_zero_when_no_data() -> None:
    summary = compute_budget(
        config=FinanceConfig.empty(),
        month_expenses=[],
        year_savings=[],
        today=date(2026, 5, 18),
    )
    assert summary.income_cents == 0
    assert summary.remaining_cents == 0
    assert summary.pending_recurring == ()


def test_remaining_equals_income_minus_pending_when_nothing_ticked() -> None:
    cfg = _config(
        RecurringItem("loyer", "Loyer", 80000, 5, "expense"),
        RecurringItem("netflix", "Netflix", 1799, 12, "expense"),
        RecurringItem("pel", "PEL", 20000, 5, "saving"),
    )
    summary = compute_budget(
        config=cfg,
        month_expenses=[_income(250000)],
        year_savings=[],
        today=date(2026, 5, 1),  # début de mois, rien encore overdue
    )
    assert summary.income_cents == 250000
    assert summary.pending_total_cents == 80000 + 1799 + 20000
    assert summary.remaining_cents == 250000 - (80000 + 1799 + 20000)
    assert summary.pending_recurring_count == 3
    assert not summary.has_overdue


def test_remaining_after_partial_ticks() -> None:
    cfg = _config(
        RecurringItem("loyer", "Loyer", 80000, 5, "expense"),
        RecurringItem("netflix", "Netflix", 1799, 12, "expense"),
    )
    summary = compute_budget(
        config=cfg,
        month_expenses=[_income(250000), _tick("loyer", 80000)],
        year_savings=[],
        today=date(2026, 5, 6),
    )
    # Loyer pointé (sorti réel), Netflix encore pending
    assert summary.spent_recurring_cents == 80000
    assert summary.pending_recurring_count == 1
    assert summary.pending_recurring[0].key == "netflix"
    assert summary.remaining_cents == 250000 - 80000 - 1799


def test_remaining_after_punctual_expenses() -> None:
    summary = compute_budget(
        config=FinanceConfig.empty(),
        month_expenses=[_income(250000), _punctual(2700), _punctual(1500)],
        year_savings=[],
        today=date(2026, 5, 18),
    )
    assert summary.spent_punctual_cents == 4200
    assert summary.remaining_cents == 250000 - 4200


def test_pending_is_overdue_when_day_lt_today() -> None:
    cfg = _config(
        RecurringItem("loyer", "Loyer", 80000, 5, "expense"),
        RecurringItem("netflix", "Netflix", 1799, 12, "expense"),
    )
    summary = compute_budget(
        config=cfg,
        month_expenses=[_income(250000)],
        year_savings=[],
        today=date(2026, 5, 10),  # 5 passé, 12 à venir
    )
    by_key = {p.key: p for p in summary.pending_recurring}
    assert by_key["loyer"].is_overdue is True
    assert by_key["netflix"].is_overdue is False
    assert summary.has_overdue is True


def test_pending_excludes_already_ticked() -> None:
    cfg = _config(
        RecurringItem("loyer", "Loyer", 80000, 5, "expense"),
        RecurringItem("pel", "PEL", 20000, 5, "saving"),
    )
    summary = compute_budget(
        config=cfg,
        month_expenses=[
            _tick("loyer", 80000, kind="recurring_tick"),
            _tick("pel", 20000, kind="saving_tick"),
        ],
        year_savings=[_tick("pel", 20000, kind="saving_tick")],
        today=date(2026, 5, 18),
    )
    assert summary.pending_recurring == ()


def test_saved_this_year_aggregates_all_saving_ticks() -> None:
    year_savings = [
        _tick("pel", 20000, kind="saving_tick"),
        _tick("pel", 20000, kind="saving_tick"),
        _tick("pel", 20000, kind="saving_tick"),
    ]
    summary = compute_budget(
        config=FinanceConfig.empty(),
        month_expenses=[],
        year_savings=year_savings,
        today=date(2026, 5, 18),
    )
    assert summary.saved_this_year_cents == 60000


def test_day_31_caps_to_short_month() -> None:
    cfg = _config(
        RecurringItem("end", "Fin de mois", 5000, 31, "expense"),
    )
    summary = compute_budget(
        config=cfg,
        month_expenses=[],
        year_savings=[],
        today=date(2026, 2, 1),
    )
    # Février 2026 a 28 jours.
    assert summary.pending_recurring[0].day == 28


def test_saving_tick_counts_in_saved_this_month_and_remaining() -> None:
    cfg = _config()
    summary = compute_budget(
        config=cfg,
        month_expenses=[_income(250000), _tick("pel", 20000, kind="saving_tick")],
        year_savings=[],
        today=date(2026, 5, 18),
    )
    assert summary.saved_this_month_cents == 20000
    assert summary.remaining_cents == 250000 - 20000


def test_month_field_is_first_of_month() -> None:
    summary = compute_budget(
        config=FinanceConfig.empty(),
        month_expenses=[],
        year_savings=[],
        today=date(2026, 5, 18),
    )
    assert summary.month == date(2026, 5, 1)
