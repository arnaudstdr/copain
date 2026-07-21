"""Tests purs de `compute_budget` (pas de SQLite, pas d'I/O)."""

from __future__ import annotations

from datetime import date

from bot.finance.budget import OPEN_CYCLE_END, compute_budget, compute_trend
from bot.finance.config import EnvelopeItem, FinanceConfig, RecurringItem
from bot.finance.models import Expense


def _config(
    *items: RecurringItem,
    envelopes: tuple[EnvelopeItem, ...] = (),
) -> FinanceConfig:
    return FinanceConfig(currency="EUR", recurring=items, envelopes=envelopes)


def _punctual_cat(cents: int, category: str, day: int = 10, shared: bool = False) -> Expense:
    return Expense(
        kind="punctual",
        amount_cents=cents,
        label="Achat",
        category=category,
        occurred_on=date(2026, 5, day),
        shared=shared,
    )


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


# --- Envelopes ----------------------------------------------------------


def test_envelope_empty_when_no_spending() -> None:
    cfg = _config(envelopes=(EnvelopeItem("essence", "Essence", 20000),))
    summary = compute_budget(
        config=cfg,
        month_expenses=[_income(250000)],
        year_savings=[],
        today=date(2026, 5, 18),
    )
    assert len(summary.envelopes) == 1
    env = summary.envelopes[0]
    assert env.spent_cents == 0
    assert env.remaining_cents == 20000
    assert not env.is_overrun
    # Allocated (200€) déduit du restant.
    assert summary.remaining_cents == 250000 - 20000


def test_envelope_partial_spending_does_not_double_count() -> None:
    """Une ponctuelle essence puise dans l'enveloppe, pas dans le restant."""
    cfg = _config(envelopes=(EnvelopeItem("essence", "Essence", 20000),))
    summary = compute_budget(
        config=cfg,
        month_expenses=[_income(250000), _punctual_cat(8000, "essence")],
        year_savings=[],
        today=date(2026, 5, 18),
    )
    env = summary.envelopes[0]
    assert env.spent_cents == 8000
    assert env.remaining_cents == 12000
    # Restant inchangé par rapport au cas zéro dépense : on déduit toujours
    # les 200€ alloués, et la ponctuelle n'y vient pas en plus.
    assert summary.remaining_cents == 250000 - 20000


def test_envelope_overrun_grignote_le_restant() -> None:
    """230€ d'essence pour 200€ alloués → 30€ d'overrun déduits du restant."""
    cfg = _config(envelopes=(EnvelopeItem("essence", "Essence", 20000),))
    summary = compute_budget(
        config=cfg,
        month_expenses=[_income(250000), _punctual_cat(23000, "essence")],
        year_savings=[],
        today=date(2026, 5, 18),
    )
    env = summary.envelopes[0]
    assert env.spent_cents == 23000
    assert env.remaining_cents == -3000  # dépassement
    assert env.overrun_cents == 3000
    assert env.is_overrun
    assert summary.has_envelope_overrun
    # Restant = revenu - allocated (200) - overrun (30) = 230€ "consommés"
    assert summary.remaining_cents == 250000 - 20000 - 3000


def test_envelope_category_matching_case_insensitive() -> None:
    cfg = _config(envelopes=(EnvelopeItem("essence", "Essence", 20000),))
    summary = compute_budget(
        config=cfg,
        month_expenses=[_income(250000), _punctual_cat(5000, "Essence")],
        year_savings=[],
        today=date(2026, 5, 18),
    )
    assert summary.envelopes[0].spent_cents == 5000


def test_punctual_outside_envelope_counts_in_remaining() -> None:
    """Une ponctuelle hors enveloppe doit bien baisser le restant."""
    cfg = _config(envelopes=(EnvelopeItem("essence", "Essence", 20000),))
    summary = compute_budget(
        config=cfg,
        month_expenses=[
            _income(250000),
            _punctual_cat(5000, "essence"),  # puise dans l'enveloppe
            _punctual_cat(2700, "pharmacie"),  # vient bien sur le restant
        ],
        year_savings=[],
        today=date(2026, 5, 18),
    )
    # Restant = revenu - allocated essence - pharmacie
    assert summary.remaining_cents == 250000 - 20000 - 2700


def test_two_envelopes_aggregates() -> None:
    cfg = _config(
        envelopes=(
            EnvelopeItem("essence", "Essence", 20000),
            EnvelopeItem("courses", "Courses", 60000),
        )
    )
    summary = compute_budget(
        config=cfg,
        month_expenses=[
            _income(250000),
            _punctual_cat(8000, "essence"),
            _punctual_cat(15000, "courses"),
        ],
        year_savings=[],
        today=date(2026, 5, 18),
    )
    assert len(summary.envelopes) == 2
    assert summary.envelopes_allocated_cents == 80000
    assert summary.envelopes_overrun_cents == 0
    # Restant = revenu - 800€ alloués - 0 overrun (les ponctuelles sont dans les enveloppes)
    assert summary.remaining_cents == 250000 - 80000


def test_no_envelope_remaining_matches_legacy_formula() -> None:
    """Invariant : sans envelope, le restant doit être identique au calcul historique."""
    cfg = _config()
    summary = compute_budget(
        config=cfg,
        month_expenses=[_income(250000), _punctual(2700)],
        year_savings=[],
        today=date(2026, 5, 18),
    )
    assert summary.envelopes == ()
    assert summary.remaining_cents == 250000 - 2700


def test_shared_envelope_does_not_affect_remaining() -> None:
    """Une enveloppe shared est purement informative : ni allocation ni overrun
    ne grignotent le restant prévisionnel perso."""
    cfg = _config(envelopes=(EnvelopeItem("nourriture", "Courses joint", 60000, shared=True),))
    summary = compute_budget(
        config=cfg,
        month_expenses=[
            _income(250000),
            _punctual_cat(15000, "nourriture", shared=True),
        ],
        year_savings=[],
        today=date(2026, 5, 18),
    )
    # L'enveloppe est exposée pour le dashboard…
    assert len(summary.envelopes) == 1
    assert summary.envelopes[0].shared is True
    assert summary.envelopes[0].spent_cents == 15000
    # …mais elle est invisible des agrégats perso.
    assert summary.envelopes_allocated_cents == 0
    assert summary.envelopes_overrun_cents == 0
    # Et la dépense shared est exclue de spent_punctual_cents.
    assert summary.spent_punctual_cents == 0
    # Donc remaining = revenu pur, rien d'autre.
    assert summary.remaining_cents == 250000


def test_shared_envelope_overrun_does_not_grignote_remaining() -> None:
    """Même un dépassement sur une enveloppe shared n'impacte pas le restant perso."""
    cfg = _config(envelopes=(EnvelopeItem("nourriture", "Courses joint", 60000, shared=True),))
    summary = compute_budget(
        config=cfg,
        month_expenses=[
            _income(250000),
            _punctual_cat(80000, "nourriture", shared=True),
        ],
        year_savings=[],
        today=date(2026, 5, 18),
    )
    assert summary.envelopes[0].is_overrun is True
    assert summary.envelopes[0].overrun_cents == 20000
    # Overrun shared = invisible côté perso.
    assert summary.remaining_cents == 250000


def test_shared_and_perso_envelopes_dont_cross_match() -> None:
    """Deux enveloppes même catégorie (perso + shared) : chacune ne matche que
    les dépenses de son périmètre."""
    cfg = _config(
        envelopes=(
            EnvelopeItem("nourriture", "Courses perso", 20000, shared=False),
            EnvelopeItem("nourriture", "Courses joint", 60000, shared=True),
        )
    )
    summary = compute_budget(
        config=cfg,
        month_expenses=[
            _income(250000),
            _punctual_cat(8000, "nourriture", shared=False),  # Lidl perso
            _punctual_cat(35000, "nourriture", shared=True),  # Lidl joint
        ],
        year_savings=[],
        today=date(2026, 5, 18),
    )
    by_label = {e.label: e for e in summary.envelopes}
    # Perso : 80€ Lidl perso uniquement
    assert by_label["Courses perso"].spent_cents == 8000
    assert by_label["Courses perso"].shared is False
    # Joint : 350€ Lidl joint uniquement
    assert by_label["Courses joint"].spent_cents == 35000
    assert by_label["Courses joint"].shared is True
    # Restant : revenu - 200€ alloués (perso) - 0 overrun.
    # La dépense shared (350€) ne grignote pas.
    assert summary.remaining_cents == 250000 - 20000


def test_shared_expense_without_envelope_is_ignored_in_remaining() -> None:
    """Cas limite : une dépense shared sans enveloppe correspondante reste
    exclue du restant perso (on ne crée pas un faux poste à comptabiliser)."""
    cfg = _config()
    summary = compute_budget(
        config=cfg,
        month_expenses=[
            _income(250000),
            _punctual_cat(15000, "nourriture", shared=True),
        ],
        year_savings=[],
        today=date(2026, 5, 18),
    )
    assert summary.spent_punctual_cents == 0
    assert summary.remaining_cents == 250000


# --- Cycle budgétaire (bornes ancrées salaire) ----------------------------


def test_cycle_start_drives_summary_month_and_end() -> None:
    cfg = _config(RecurringItem("loyer", "Loyer", 80000, 5, "expense"))
    summary = compute_budget(
        config=cfg,
        month_expenses=[_income(250000, day=28, month=4)],
        year_savings=[],
        today=date(2026, 5, 3),
        cycle_start=date(2026, 4, 28),
        cycle_end=date(2026, 5, 30),
    )
    assert summary.month == date(2026, 4, 28)
    assert summary.cycle_end == date(2026, 5, 30)


def test_pending_due_date_projected_into_cycle() -> None:
    cfg = _config(RecurringItem("loyer", "Loyer", 80000, 5, "expense"))
    summary = compute_budget(
        config=cfg,
        month_expenses=[],
        year_savings=[],
        today=date(2026, 5, 3),
        cycle_start=date(2026, 4, 28),
        cycle_end=date(2026, 5, 30),
    )
    pending = summary.pending_recurring[0]
    # « le 5 » à partir du 28/04 tombe le 05/05.
    assert pending.day == 5
    assert pending.is_overdue is False  # 03/05 < 05/05


def test_pending_overdue_within_cycle() -> None:
    cfg = _config(RecurringItem("loyer", "Loyer", 80000, 5, "expense"))
    summary = compute_budget(
        config=cfg,
        month_expenses=[],
        year_savings=[],
        today=date(2026, 5, 10),
        cycle_start=date(2026, 4, 28),
        cycle_end=date(2026, 5, 30),
    )
    pending = summary.pending_recurring[0]
    assert pending.day == 5
    assert pending.is_overdue is True  # 10/05 > 05/05


# --- Tendance : projection + rythme + courbe (compute_trend) --------------


def _perso_punctual(cents: int, day: int, category: str | None = None) -> Expense:
    return Expense(
        kind="punctual",
        amount_cents=cents,
        label="Achat",
        category=category,
        occurred_on=date(2026, 5, day),
        shared=False,
    )


def test_trend_happy_path() -> None:
    """Cycle civil 01→31/05, 10 jours écoulés au 11/05.

    Ponctuelles perso : 80€ essence (enveloppe) + 40€ hors enveloppe.
    Une dépense shared de 50€ (exclue partout). Loyer pointé, PEL pending.
    """
    cfg = _config(
        RecurringItem("loyer", "Loyer", 80000, 5, "expense"),
        RecurringItem("pel", "PEL", 20000, 5, "saving"),
        envelopes=(EnvelopeItem("essence", "Essence", 20000),),
    )
    month_expenses = [
        _income(250000),
        _perso_punctual(8000, day=3, category="essence"),  # dans l'enveloppe
        _perso_punctual(4000, day=8),  # hors enveloppe
        _punctual_cat(5000, "nourriture", day=6, shared=True),  # shared, exclu
        _tick("loyer", 80000),  # récurrente pointée
    ]
    summary = compute_budget(
        config=cfg,
        month_expenses=month_expenses,
        year_savings=[],
        today=date(2026, 5, 11),
        cycle_start=date(2026, 5, 1),
        cycle_end=date(2026, 5, 31),
    )
    # remaining = 250000 - 4000 (hors env) - 80000 (loyer) - 20000 (pel pending)
    #             - 20000 (alloc essence) - 0 (overrun) = 126000
    assert summary.remaining_cents == 126000

    trend = compute_trend(summary=summary, month_expenses=month_expenses, today=date(2026, 5, 11))

    # rythme = ponctuelles perso HORS enveloppes (4000) / (10 jours écoulés + 1
    # = 11 jours civils du 01 au 11 inclus, même fenêtre que le numérateur) = 364
    assert trend.daily_rate_cents == 364
    # projection = remaining (126000) - rythme (364) * jours restants STRICTEMENT
    # après aujourd'hui (31/05 - 11/05 - 1 = 19) = 119084
    assert trend.projected_remaining_cents == 119084
    # spendable = income - récurrentes totales (loyer 80000) - épargne (pel 20000 pending) = 150000
    assert trend.spendable_cents == 150000
    # courbe : un point par jour du 01 au 11 inclus = 11 points
    assert len(trend.spend_curve) == 11
    assert trend.spend_curve[0].day == date(2026, 5, 1)
    assert trend.spend_curve[0].cumulative_cents == 0
    assert trend.spend_curve[-1].day == date(2026, 5, 11)
    # cumul final = ponctuelles perso enveloppes incluses (8000 + 4000), shared exclu
    assert trend.spend_curve[-1].cumulative_cents == 12000
    # palier au 03 (essence) et au 08 (hors env)
    by_day = {p.day: p.cumulative_cents for p in trend.spend_curve}
    assert by_day[date(2026, 5, 2)] == 0
    assert by_day[date(2026, 5, 3)] == 8000
    assert by_day[date(2026, 5, 7)] == 8000
    assert by_day[date(2026, 5, 8)] == 12000


def test_trend_day_zero_no_division_and_flat_projection() -> None:
    """Jour du salaire (today == cycle_start) : rythme 0, projection = restant, 1 point."""
    cfg = _config()
    month_expenses = [_income(250000), _perso_punctual(4000, day=5)]
    summary = compute_budget(
        config=cfg,
        month_expenses=month_expenses,
        year_savings=[],
        today=date(2026, 5, 5),
        cycle_start=date(2026, 5, 5),
        cycle_end=date(2026, 6, 5),
    )
    trend = compute_trend(summary=summary, month_expenses=month_expenses, today=date(2026, 5, 5))
    assert trend.daily_rate_cents == 0
    assert trend.projected_remaining_cents == summary.remaining_cents
    # « 1 seul jour » : un unique point (le jour du cycle_start).
    assert len(trend.spend_curve) == 1
    assert trend.spend_curve[0].day == date(2026, 5, 5)
    assert trend.spend_curve[0].cumulative_cents == 4000


def test_trend_open_cycle_projects_to_same_day_next_month() -> None:
    """Cycle ouvert (cycle_end sentinelle) : borne de projection = cycle_start + 1 mois."""
    cfg = _config()
    month_expenses = [_income(250000), _perso_punctual(3000, day=1)]  # occurred 01/05
    summary = compute_budget(
        config=cfg,
        month_expenses=month_expenses,
        year_savings=[],
        today=date(2026, 5, 10),
        cycle_start=date(2026, 4, 28),
        cycle_end=OPEN_CYCLE_END,
    )
    trend = compute_trend(summary=summary, month_expenses=month_expenses, today=date(2026, 5, 10))
    # rythme = 3000 / (10/05 - 28/04 = 12 jours écoulés + 1 = 13) = 231
    assert trend.daily_rate_cents == 231
    # borne nominale (horizon) = 28/05 ; jours restants STRICTEMENT après
    # aujourd'hui = 28/05 - 10/05 - 1 = 17 ; projection = remaining - 231 * 17
    assert trend.spend_horizon == date(2026, 5, 28)
    assert trend.projected_remaining_cents == summary.remaining_cents - 231 * 17


def test_trend_flat_curve_when_days_without_spending() -> None:
    """Jours sans dépense : la courbe reste plate (un point par jour quand même)."""
    cfg = _config()
    month_expenses = [_income(250000), _perso_punctual(5000, day=2)]
    summary = compute_budget(
        config=cfg,
        month_expenses=month_expenses,
        year_savings=[],
        today=date(2026, 5, 5),
        cycle_start=date(2026, 5, 1),
        cycle_end=date(2026, 5, 31),
    )
    trend = compute_trend(summary=summary, month_expenses=month_expenses, today=date(2026, 5, 5))
    cumulatives = [p.cumulative_cents for p in trend.spend_curve]
    assert cumulatives == [0, 5000, 5000, 5000, 5000]  # 01,02,03,04,05


def test_trend_same_day_multiple_expenses_cumulate() -> None:
    cfg = _config()
    month_expenses = [
        _income(250000),
        _perso_punctual(3000, day=3),
        _perso_punctual(2000, day=3),
    ]
    summary = compute_budget(
        config=cfg,
        month_expenses=month_expenses,
        year_savings=[],
        today=date(2026, 5, 3),
        cycle_start=date(2026, 5, 1),
        cycle_end=date(2026, 5, 31),
    )
    trend = compute_trend(summary=summary, month_expenses=month_expenses, today=date(2026, 5, 3))
    assert trend.spend_curve[-1].cumulative_cents == 5000


def test_trend_spendable_income_minus_recurring_and_savings() -> None:
    cfg = _config(
        RecurringItem("loyer", "Loyer", 80000, 5, "expense"),
        RecurringItem("netflix", "Netflix", 1799, 12, "expense"),
        RecurringItem("pel", "PEL", 20000, 5, "saving"),
    )
    month_expenses = [_income(250000), _tick("loyer", 80000)]
    summary = compute_budget(
        config=cfg,
        month_expenses=month_expenses,
        year_savings=[],
        today=date(2026, 5, 10),
        cycle_start=date(2026, 5, 1),
        cycle_end=date(2026, 5, 31),
    )
    trend = compute_trend(summary=summary, month_expenses=month_expenses, today=date(2026, 5, 10))
    # loyer 80000 (pointé) + netflix 1799 (pending) = récurrentes ; pel 20000 = épargne
    assert trend.spendable_cents == 250000 - (80000 + 1799) - 20000


def test_trend_spendable_can_be_negative() -> None:
    """spendable ≤ 0 (récurrentes > revenu) : renvoyé négatif, le front ne trace pas la cible."""
    cfg = _config(RecurringItem("loyer", "Loyer", 80000, 5, "expense"))
    month_expenses = [_income(50000)]
    summary = compute_budget(
        config=cfg,
        month_expenses=month_expenses,
        year_savings=[],
        today=date(2026, 5, 1),
        cycle_start=date(2026, 5, 1),
        cycle_end=date(2026, 5, 31),
    )
    trend = compute_trend(summary=summary, month_expenses=month_expenses, today=date(2026, 5, 1))
    assert trend.spendable_cents == 50000 - 80000
