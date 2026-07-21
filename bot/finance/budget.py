"""Calcul du restant prévisionnel du mois + agrégat épargne annuelle.

Pure functions, sans I/O. Entrées : la config YAML (récurrentes), les
écritures SQL du mois courant, les ticks d'épargne de l'année. Sortie :
un `BudgetSummary` consommable directement par le dashboard.
"""

from __future__ import annotations

import calendar as _calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from bot.finance.config import EnvelopeItem, FinanceConfig, RecurringItem, RecurringKind
from bot.finance.models import Expense

# Borne haute sentinelle d'un cycle budgétaire encore ouvert (pas d'ancre
# suivante connue). Vit ici, dans le module pur d'arithmétique de cycle ;
# `manager.py` la ré-exporte pour compatibilité.
OPEN_CYCLE_END: date = date(9999, 12, 31)


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
    """État courant d'une enveloppe budgétaire mensuelle.

    `shared=True` : enveloppe sur compte joint, purement informative côté
    dashboard. Elle ne grignote pas le restant prévisionnel perso.
    """

    category: str
    label: str
    allocated_cents: int
    spent_cents: int  # somme des ponctuelles avec cette catégorie ce mois
    overrun_cents: int  # max(0, spent - allocated)
    shared: bool = False

    @property
    def remaining_cents(self) -> int:
        """Reste dans l'enveloppe (peut être négatif en cas de dépassement)."""
        return self.allocated_cents - self.spent_cents

    @property
    def is_overrun(self) -> bool:
        return self.spent_cents > self.allocated_cents


@dataclass(frozen=True, slots=True)
class BudgetSummary:
    """État agrégé exposé par la card Budget du dashboard.

    `month` porte le **début du cycle budgétaire courant** (jour du salaire
    reçu, ou 1er du mois civil en l'absence d'ancre). `cycle_end` est la
    borne haute exclue : la prochaine ancre, ou une date sentinelle quand le
    cycle est encore ouvert. Le nom `month` est conservé pour ne pas casser
    l'API/le front existants — il signifie désormais « début de cycle ».
    """

    month: date  # début du cycle (= jour du salaire, ou 1er du mois en fallback)
    income_cents: int
    spent_punctual_cents: int
    spent_recurring_cents: int  # somme des kind=recurring_tick du cycle
    saved_this_month_cents: int  # somme des kind=saving_tick du cycle
    pending_recurring: tuple[PendingRecurring, ...]
    saved_this_year_cents: int  # cumul kind=saving_tick depuis le 1er janvier
    envelopes: tuple[EnvelopeStatus, ...] = ()
    cycle_end: date | None = None  # borne haute exclue du cycle (None = inconnue)

    @property
    def pending_total_cents(self) -> int:
        return sum(p.amount_cents for p in self.pending_recurring)

    @property
    def envelopes_allocated_cents(self) -> int:
        """Allocation totale des enveloppes PERSO (exclut les enveloppes shared)."""
        return sum(e.allocated_cents for e in self.envelopes if not e.shared)

    @property
    def envelopes_spent_in_cents(self) -> int:
        """Ce qui a déjà été consommé dans les enveloppes perso (capé à l'allocation)."""
        return sum(min(e.spent_cents, e.allocated_cents) for e in self.envelopes if not e.shared)

    @property
    def envelopes_overrun_cents(self) -> int:
        """Overrun cumulé des enveloppes PERSO (les shared n'impactent pas le restant)."""
        return sum(e.overrun_cents for e in self.envelopes if not e.shared)

    @property
    def punctual_outside_envelopes_cents(self) -> int:
        """Ponctuelles perso qui NE puisent dans aucune enveloppe perso.

        `spent_punctual_cents` exclut déjà les dépenses shared en amont ; on
        en retire la part consommée dans les enveloppes perso pour ne garder
        que les ponctuelles « libres ». Sert de source unique à `remaining_cents`
        et au rythme de dépense de `compute_trend`.
        """
        in_envelopes = sum(e.spent_cents for e in self.envelopes if not e.shared)
        return self.spent_punctual_cents - in_envelopes

    @property
    def remaining_cents(self) -> int:
        """Restant previsionnel (uniquement périmètre perso).

        Les ponctuelles déjà passées sous une enveloppe PERSO NE comptent PAS
        une deuxième fois (elles puisent dans l'enveloppe, pas dans le
        restant). En revanche, le débordement (overrun) vient bien grignoter
        le restant — sinon on mentirait à l'utilisateur.

        Les dépenses `shared=True` (compte joint) sont déjà exclues de
        `spent_punctual_cents` en amont (cf. `compute_budget`) ; les
        enveloppes shared sont absentes des agrégats `envelopes_allocated_*`
        et `envelopes_overrun_*` ci-dessus. Elles n'apparaissent donc nulle
        part dans ce calcul.

        = revenu
          - punctual_hors_enveloppes (perso uniquement)
          - recurring_tick
          - saving_tick
          - pending récurrentes
          - allocated total des enveloppes perso
          - overrun total des enveloppes perso
        """
        # Les ponctuelles consommées dans les enveloppes PERSO ont déjà été
        # "soustraites" via l'allocation + l'overrun ; on ne garde que les
        # ponctuelles hors enveloppes pour ne pas les compter deux fois.
        out = (
            self.punctual_outside_envelopes_cents
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


@dataclass(frozen=True, slots=True)
class SpendPoint:
    """Un point de la courbe de dépense cumulée (un par jour du cycle écoulé)."""

    day: date
    cumulative_cents: int


@dataclass(frozen=True, slots=True)
class BudgetTrend:
    """Tendance du cycle : projection fin de cycle + courbe de rythme.

    Calculé à part de `BudgetSummary` car dépend de `today` (que le summary,
    frozen, ne stocke pas). Toutes les valeurs sont figées à `today` — pas de
    property dépendante de l'horloge.
    """

    daily_rate_cents: int  # rythme quotidien constaté (ponctuelles hors env / jours écoulés)
    projected_remaining_cents: int  # restant extrapolé à la fin du cycle
    spendable_cents: int  # cible « rythme idéal » = income - récurrentes - épargne (peut être ≤ 0)
    spend_curve: tuple[SpendPoint, ...]  # cumul journalier des ponctuelles perso (env. incluses)
    spend_horizon: date  # fin de cycle visée par la projection (borne haute exclue / horizon)


def compute_budget(
    *,
    config: FinanceConfig,
    month_expenses: Sequence[Expense],
    year_savings: Sequence[Expense],
    today: date,
    cycle_start: date | None = None,
    cycle_end: date | None = None,
) -> BudgetSummary:
    """Compose un `BudgetSummary` à partir des sources de données.

    `cycle_start` / `cycle_end` délimitent le cycle budgétaire courant
    (bornes calculées par `ExpenseManager.current_cycle_bounds`). Quand ils
    sont omis, on retombe sur le mois civil (1er → 1er du mois suivant) :
    comportement historique, conservé pour la rétro-compatibilité.

    `month_expenses` doit déjà être filtré sur la fenêtre du cycle (par
    `list_for_cycle`) ; le nom est conservé pour limiter la diffusion du
    changement.
    """
    if cycle_start is None:
        cycle_start = today.replace(day=1)
    if cycle_end is None:
        cycle_end = _first_of_next_month(cycle_start)

    income_cents = sum(e.amount_cents for e in month_expenses if e.kind == "income")
    # Les ponctuelles `shared=True` (compte joint) sont hors-périmètre perso.
    # Elles restent matchées par leur enveloppe shared via `_envelopes_status`
    # pour l'affichage, mais n'entrent pas dans le restant prévisionnel.
    spent_punctual = sum(
        e.amount_cents for e in month_expenses if e.kind == "punctual" and not e.shared
    )
    spent_recurring = sum(e.amount_cents for e in month_expenses if e.kind == "recurring_tick")
    saved_this_month = sum(e.amount_cents for e in month_expenses if e.kind == "saving_tick")

    ticked_keys = {
        e.recurring_key
        for e in month_expenses
        if e.kind in {"recurring_tick", "saving_tick"} and e.recurring_key is not None
    }

    pending = tuple(_pending_for_cycle(config.recurring, ticked_keys, today, cycle_start))
    envelopes = tuple(_envelopes_status(config.envelopes, month_expenses))

    saved_this_year = sum(e.amount_cents for e in year_savings if e.kind == "saving_tick")

    return BudgetSummary(
        month=cycle_start,
        income_cents=income_cents,
        spent_punctual_cents=spent_punctual,
        spent_recurring_cents=spent_recurring,
        saved_this_month_cents=saved_this_month,
        pending_recurring=pending,
        saved_this_year_cents=saved_this_year,
        envelopes=envelopes,
        cycle_end=cycle_end,
    )


def compute_trend(
    *,
    summary: BudgetSummary,
    month_expenses: Sequence[Expense],
    today: date,
) -> BudgetTrend:
    """Projection fin de cycle + courbe de dépense journalière.

    Fonction pure : les bornes du cycle sont lues sur `summary` (`month` =
    début, `cycle_end` = borne haute exclue ou sentinelle `OPEN_CYCLE_END`).

    - `daily_rate_cents` : ponctuelles perso HORS enveloppes / jours écoulés
      (0 au jour du salaire, pas de division par zéro) ;
    - `projected_remaining_cents` : `remaining - rythme x jours restants`,
      horizon = `cycle_end` (cycle fermé) ou `cycle_start + 1 mois` (ouvert) ;
    - `spendable_cents` : `income - récurrentes totales (pointées + pending)
      - épargne` — cible de la droite « rythme idéal » (peut être <= 0) ;
    - `spend_curve` : un point par jour de `cycle_start` à `today` inclus,
      cumul des ponctuelles perso, **enveloppes incluses**, shared exclu.
    """
    cycle_start = summary.month
    elapsed_days = (today - cycle_start).days

    if elapsed_days <= 0:
        daily_rate = 0
    else:
        # Le numérateur (`punctual_outside_envelopes_cents`) agrège les dépenses
        # de `cycle_start` à `today` INCLUS, soit `elapsed_days + 1` jours
        # civils : on divise par ce même nombre pour que numérateur et
        # dénominateur portent sur la même fenêtre (sinon le rythme est
        # surévalué, surtout juste après le salaire).
        daily_rate = round(summary.punctual_outside_envelopes_cents / (elapsed_days + 1))

    end = summary.cycle_end
    horizon = _same_day_next_month(cycle_start) if end is None or end >= OPEN_CYCLE_END else end
    # Les dépenses d'aujourd'hui sont déjà décomptées dans `remaining_cents` :
    # on ne projette le rythme que sur les jours STRICTEMENT après aujourd'hui
    # (`horizon` est une borne exclue, `(horizon - today).days` inclut today).
    remaining_days = max(0, (horizon - today).days - 1)
    projected_remaining = summary.remaining_cents - daily_rate * remaining_days

    spendable = (
        summary.income_cents
        - summary.spent_recurring_cents
        - summary.saved_this_month_cents
        - summary.pending_total_cents
    )

    curve = _spend_curve(month_expenses, cycle_start, today)

    return BudgetTrend(
        daily_rate_cents=daily_rate,
        projected_remaining_cents=projected_remaining,
        spendable_cents=spendable,
        spend_curve=curve,
        spend_horizon=horizon,
    )


def _spend_curve(
    month_expenses: Sequence[Expense],
    cycle_start: date,
    today: date,
) -> tuple[SpendPoint, ...]:
    """Cumul journalier des ponctuelles perso (enveloppes incluses, shared exclu).

    Un point par jour de `cycle_start` à `today` inclus ; les jours sans
    dépense reportent le cumul précédent (courbe plate).
    """
    daily_totals: dict[date, int] = {}
    for e in month_expenses:
        if e.kind != "punctual" or e.shared:
            continue
        daily_totals[e.occurred_on] = daily_totals.get(e.occurred_on, 0) + e.amount_cents

    points: list[SpendPoint] = []
    cumulative = 0
    day = cycle_start
    while day <= today:
        cumulative += daily_totals.get(day, 0)
        points.append(SpendPoint(day=day, cumulative_cents=cumulative))
        day += timedelta(days=1)
    return tuple(points)


def _first_of_next_month(d: date) -> date:
    """1er du mois suivant le mois de `d` (borne haute exclue du mois civil)."""
    first = d.replace(day=1)
    if first.month == 12:
        return first.replace(year=first.year + 1, month=1)
    return first.replace(month=first.month + 1)


def _same_day_next_month(d: date) -> date:
    """Même quantième le mois suivant (capé au dernier jour pour les mois courts).

    Durée nominale d'un cycle de paie ancré le 28 : 28/04 → 28/05, 31/01 → 28/02.
    """
    if d.month == 12:
        year, month = d.year + 1, 1
    else:
        year, month = d.year, d.month + 1
    last = _calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last))


def next_recurring_occurrence(day: int, on_or_after: date) -> date:
    """Première date dont le jour-du-mois vaut `day` (capé), à partir de `on_or_after`.

    Sert à projeter une récurrente déclarée « le 5 » dans la fenêtre du
    cycle courant : si le cycle démarre le 28/04, le « 5 » tombe le 05/05.
    """
    last = _calendar.monthrange(on_or_after.year, on_or_after.month)[1]
    candidate = on_or_after.replace(day=min(day, last))
    if candidate >= on_or_after:
        return candidate
    # Le jour est déjà passé dans le mois de départ → mois suivant.
    if on_or_after.month == 12:
        year, month = on_or_after.year + 1, 1
    else:
        year, month = on_or_after.year, on_or_after.month + 1
    last_next = _calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_next))


def _envelopes_status(
    envelopes: Sequence[EnvelopeItem],
    month_expenses: Sequence[Expense],
) -> list[EnvelopeStatus]:
    """Pour chaque enveloppe, calcule le montant consommé par les ponctuelles.

    Le matching croise deux dimensions :
    - `category` (insensible à la casse et aux espaces, pour encaisser les
      variations du LLM "Essence" vs "essence") ;
    - `shared` : une enveloppe shared ne matche QUE les dépenses shared, et
      inversement. Évite qu'une "course Lidl perso" pollue l'enveloppe joint
      nourriture (et vice-versa).
    """
    if not envelopes:
        return []
    # Clé : (category_lower, shared_flag) → cents consommés.
    spent_by_bucket: dict[tuple[str, bool], int] = {}
    for e in month_expenses:
        if e.kind != "punctual" or not e.category:
            continue
        bucket = (e.category.strip().lower(), bool(e.shared))
        spent_by_bucket[bucket] = spent_by_bucket.get(bucket, 0) + e.amount_cents

    out: list[EnvelopeStatus] = []
    for env in envelopes:
        bucket = (env.category.strip().lower(), env.shared)
        spent = spent_by_bucket.get(bucket, 0)
        overrun = max(0, spent - env.amount_cents)
        out.append(
            EnvelopeStatus(
                category=env.category,
                label=env.label,
                allocated_cents=env.amount_cents,
                spent_cents=spent,
                overrun_cents=overrun,
                shared=env.shared,
            )
        )
    return out


def _pending_for_cycle(
    recurring: Sequence[RecurringItem],
    ticked_keys: set[str],
    today: date,
    cycle_start: date,
) -> list[PendingRecurring]:
    """Récurrentes non pointées, avec leur échéance projetée dans le cycle.

    L'échéance est la première occurrence du jour déclaré (`item.day`) à
    partir du début du cycle. En l'absence d'ancre (cycle = mois civil
    commençant le 1er), cette projection coïncide avec l'ancien
    `clamp_day_to_month` et le critère `is_overdue` reste identique.
    """
    pending: list[PendingRecurring] = []
    for item in recurring:
        if item.key in ticked_keys:
            continue
        due = next_recurring_occurrence(item.day, cycle_start)
        pending.append(
            PendingRecurring(
                key=item.key,
                label=item.label,
                amount_cents=item.amount_cents,
                day=due.day,
                kind=item.kind,
                is_overdue=due < today,
            )
        )
    return pending
