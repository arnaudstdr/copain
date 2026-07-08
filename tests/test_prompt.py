"""Tests du bloc "État du budget" injecté dans le system prompt."""

from __future__ import annotations

from datetime import date

from bot.finance.budget import BudgetSummary, EnvelopeStatus, PendingRecurring
from bot.llm.prompt import _format_budget_section


def _summary(**overrides: object) -> BudgetSummary:
    base: dict[str, object] = {
        "month": date(2026, 7, 1),
        "income_cents": 250000,
        "spent_punctual_cents": 40000,
        "spent_recurring_cents": 60000,
        "saved_this_month_cents": 10000,
        "pending_recurring": (),
        "saved_this_year_cents": 70000,
        "envelopes": (),
        "cycle_end": date(2026, 8, 1),
    }
    base.update(overrides)
    return BudgetSummary(**base)  # type: ignore[arg-type]


def test_format_budget_section_none_is_empty() -> None:
    assert _format_budget_section(None) == ""


def test_format_budget_section_healthy_has_no_alerts() -> None:
    block = _format_budget_section(_summary())
    assert "--- État du budget (mois en cours) ---" in block
    assert "Restant prévisionnel" in block
    assert "sans dramatiser" in block  # cadrage de ton présent
    assert "en retard" not in block
    assert "dépassée" not in block


def test_format_budget_section_flags_overdue_recurring() -> None:
    pending = (
        PendingRecurring(
            key="loyer", label="Loyer", amount_cents=50000, day=5, kind="expense", is_overdue=True
        ),
    )
    block = _format_budget_section(_summary(pending_recurring=pending))
    assert "Récurrentes encore à pointer : 1" in block
    assert "en retard" in block


def test_format_budget_section_flags_envelope_overrun() -> None:
    envelopes = (
        EnvelopeStatus(
            category="courses",
            label="Courses",
            allocated_cents=30000,
            spent_cents=35000,
            overrun_cents=5000,
        ),
    )
    block = _format_budget_section(_summary(envelopes=envelopes))
    assert "enveloppe est dépassée" in block
