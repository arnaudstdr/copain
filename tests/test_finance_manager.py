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


async def test_list_between_inclusive_bounds_and_ascending(
    manager: ExpenseManager,
) -> None:
    # Hors borne basse (la veille du `start`)
    await manager.add_punctual(
        amount_cents=100, label="Trop tôt", category=None, occurred_on=date(2026, 4, 30)
    )
    # Pile la borne basse
    await manager.add_income(amount_cents=250000, label="Salaire mai", occurred_on=date(2026, 5, 1))
    # Au milieu
    await manager.add_punctual(
        amount_cents=2700,
        label="Pharmacie",
        category="santé",
        occurred_on=date(2026, 5, 10),
    )
    # Pile la borne haute (doit être inclus, à la différence de list_for_month)
    await manager.tick_recurring(
        recurring_key="loyer",
        label="Loyer",
        amount_cents=80000,
        kind="expense",
        occurred_on=date(2026, 5, 31),
    )
    # Hors borne haute (le lendemain)
    await manager.add_punctual(
        amount_cents=999, label="Trop tard", category=None, occurred_on=date(2026, 6, 1)
    )

    rows = await manager.list_between(date(2026, 5, 1), date(2026, 5, 31))
    assert [r.label for r in rows] == ["Salaire mai", "Pharmacie", "Loyer"]


async def test_list_between_empty_when_no_match(manager: ExpenseManager) -> None:
    await manager.add_punctual(
        amount_cents=100, label="Mai", category=None, occurred_on=date(2026, 5, 10)
    )
    rows = await manager.list_between(date(2026, 6, 1), date(2026, 6, 30))
    assert rows == []


def test_clamp_day_to_month_caps_february() -> None:
    assert clamp_day_to_month(31, date(2026, 2, 1)) == 28
    assert clamp_day_to_month(31, date(2024, 2, 1)) == 29  # année bissextile
    assert clamp_day_to_month(31, date(2026, 4, 1)) == 30
    assert clamp_day_to_month(15, date(2026, 5, 1)) == 15


# --- Cycles budgétaires (ancrage salaire) ---------------------------------


async def test_cycle_bounds_falls_back_to_civil_month_when_no_anchor(
    manager: ExpenseManager,
) -> None:
    start, end = await manager.current_cycle_bounds(date(2026, 5, 18))
    assert start == date(2026, 5, 1)
    assert end == date(2026, 6, 1)


async def test_cycle_bounds_open_after_single_anchor(manager: ExpenseManager) -> None:
    await manager.start_cycle(date(2026, 4, 28))
    start, end = await manager.current_cycle_bounds(date(2026, 5, 18))
    assert start == date(2026, 4, 28)
    assert end == date(9999, 12, 31)  # cycle ouvert (pas de salaire suivant)


async def test_cycle_bounds_window_between_two_anchors(manager: ExpenseManager) -> None:
    await manager.start_cycle(date(2026, 4, 28))
    await manager.start_cycle(date(2026, 5, 30))
    # Aujourd'hui = 18/05 → on est dans le cycle ouvert le 28/04, borné par le 30/05.
    start, end = await manager.current_cycle_bounds(date(2026, 5, 18))
    assert start == date(2026, 4, 28)
    assert end == date(2026, 5, 30)
    # Aujourd'hui = 02/06 → on bascule dans le cycle ouvert le 30/05.
    start2, end2 = await manager.current_cycle_bounds(date(2026, 6, 2))
    assert start2 == date(2026, 5, 30)
    assert end2 == date(9999, 12, 31)


async def test_cycle_bounds_civil_fallback_when_today_precedes_first_anchor(
    manager: ExpenseManager,
) -> None:
    await manager.start_cycle(date(2026, 5, 30))
    start, end = await manager.current_cycle_bounds(date(2026, 5, 18))
    assert start == date(2026, 5, 1)
    assert end == date(2026, 6, 1)


async def test_start_cycle_is_idempotent_on_same_date(manager: ExpenseManager) -> None:
    first = await manager.start_cycle(date(2026, 4, 28))
    second = await manager.start_cycle(date(2026, 4, 28))
    assert first.id == second.id


async def test_list_for_cycle_scopes_to_anchor_window(manager: ExpenseManager) -> None:
    await manager.start_cycle(date(2026, 4, 28))
    await manager.start_cycle(date(2026, 5, 30))
    # Dans le cycle 28/04 → 30/05
    await manager.add_income(amount_cents=250000, label="Salaire", occurred_on=date(2026, 4, 28))
    await manager.add_punctual(
        amount_cents=2700, label="Pharmacie", category=None, occurred_on=date(2026, 5, 10)
    )
    # Avant l'ancre (cycle précédent)
    await manager.add_punctual(
        amount_cents=1500, label="Vieux", category=None, occurred_on=date(2026, 4, 20)
    )
    # Dans le cycle suivant
    await manager.add_punctual(
        amount_cents=999, label="Futur", category=None, occurred_on=date(2026, 5, 31)
    )
    rows = await manager.list_for_cycle(date(2026, 5, 18))
    assert {r.label for r in rows} == {"Salaire", "Pharmacie"}


async def test_is_recurring_ticked_in_cycle_isolates_cycles(manager: ExpenseManager) -> None:
    await manager.start_cycle(date(2026, 4, 28))
    await manager.start_cycle(date(2026, 5, 30))
    await manager.tick_recurring(
        recurring_key="loyer",
        label="Loyer",
        amount_cents=80000,
        kind="expense",
        occurred_on=date(2026, 5, 5),  # dans le cycle 28/04 → 30/05
    )
    assert await manager.is_recurring_ticked_in_cycle("loyer", date(2026, 5, 18))
    # Cycle suivant : pas encore pointé.
    assert not await manager.is_recurring_ticked_in_cycle("loyer", date(2026, 6, 2))


async def test_tick_recurring_once_returns_none_when_already_ticked(
    manager: ExpenseManager,
) -> None:
    first = await manager.tick_recurring_once(
        recurring_key="loyer",
        label="Loyer",
        amount_cents=80000,
        kind="expense",
        occurred_on=date(2026, 5, 5),
    )
    second = await manager.tick_recurring_once(
        recurring_key="loyer",
        label="Loyer",
        amount_cents=80000,
        kind="expense",
        occurred_on=date(2026, 5, 18),  # même cycle (mois civil sans ancre)
    )
    assert first is not None
    assert second is None


async def test_tick_recurring_once_is_atomic_under_concurrency(
    manager: ExpenseManager,
) -> None:
    """Deux pointages concurrents de la même récurrente → une seule écriture.

    Régression du check-then-act SELECT puis INSERT qui pouvait doubler un
    tick si deux requêtes arrivaient en parallèle.
    """
    import asyncio

    results = await asyncio.gather(
        *(
            manager.tick_recurring_once(
                recurring_key="pel",
                label="Versement PEL",
                amount_cents=20000,
                kind="saving",
                occurred_on=date(2026, 5, 5),
            )
            for _ in range(5)
        )
    )
    created = [r for r in results if r is not None]
    assert len(created) == 1
    rows = await manager.list_for_month(date(2026, 5, 1))
    assert len(rows) == 1


# --- update / delete (correction de saisie, step 03) ------------------------


async def test_update_applies_only_provided_fields(manager: ExpenseManager) -> None:
    """Update partiel : seuls les champs non-None sont écrits (sémantique PATCH)."""
    original = await manager.add_punctual(
        amount_cents=2700,
        label="Pharmacie",
        category="santé",
        occurred_on=date(2026, 5, 18),
    )

    updated = await manager.update(original.id, amount_cents=3100)

    assert updated is not None
    assert updated.id == original.id
    assert updated.amount_cents == 3100  # changé
    assert updated.label == "Pharmacie"  # inchangé
    assert updated.category == "santé"  # inchangé
    assert updated.occurred_on == date(2026, 5, 18)  # inchangé

    # Persistance vérifiée par relecture.
    rows = await manager.list_for_month(date(2026, 5, 1))
    assert [(r.amount_cents, r.label) for r in rows] == [(3100, "Pharmacie")]


async def test_update_can_change_several_fields_at_once(manager: ExpenseManager) -> None:
    original = await manager.add_punctual(
        amount_cents=1000,
        label="Resto",
        category=None,
        occurred_on=date(2026, 5, 10),
        shared=False,
    )

    updated = await manager.update(
        original.id,
        label="Restaurant",
        category="sorties",
        occurred_on=date(2026, 5, 12),
        shared=True,
    )

    assert updated is not None
    assert updated.amount_cents == 1000  # non fourni → inchangé
    assert updated.label == "Restaurant"
    assert updated.category == "sorties"
    assert updated.occurred_on == date(2026, 5, 12)
    assert updated.shared is True


async def test_update_returns_none_for_unknown_id(manager: ExpenseManager) -> None:
    assert await manager.update(999, amount_cents=500) is None


async def test_delete_removes_row(manager: ExpenseManager) -> None:
    expense = await manager.add_punctual(
        amount_cents=2700,
        label="Pharmacie",
        category="santé",
        occurred_on=date(2026, 5, 18),
    )

    assert await manager.delete(expense.id) is True

    rows = await manager.list_for_month(date(2026, 5, 1))
    assert rows == []


async def test_delete_returns_false_for_unknown_id(manager: ExpenseManager) -> None:
    assert await manager.delete(999) is False


async def test_delete_recurring_tick_makes_it_pending_again(manager: ExpenseManager) -> None:
    """Supprimer un tick « dépointe » la récurrente (redevient pending)."""
    tick = await manager.tick_recurring(
        recurring_key="loyer",
        label="Loyer",
        amount_cents=80000,
        kind="expense",
        occurred_on=date(2026, 5, 5),
    )
    assert await manager.is_recurring_ticked_in_cycle("loyer", date(2026, 5, 18))

    assert await manager.delete(tick.id) is True

    assert not await manager.is_recurring_ticked_in_cycle("loyer", date(2026, 5, 18))
