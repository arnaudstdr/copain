"""Side effects par intent : memory, task + rappel, dépôt cognitif, finances.

Appliqués par les orchestrateurs après extraction du bloc `<meta>`, avant
le dispatch des handlers : on persiste d'abord (SQLite / ChromaDB), le texte
de réponse vient ensuite. `BotDeps` n'est importé que sous TYPE_CHECKING
(`core` importe ce module au runtime, jamais l'inverse).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from bot.logging_conf import get_logger
from bot.pipeline.dates import parse_due, parse_when_to_date

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bot.finance.budget import PendingRecurring
    from bot.llm.parser import Meta
    from bot.pipeline.core import BotDeps

log = get_logger(__name__)


async def apply_side_effects(
    user_text: str,
    meta: Meta,
    deps: BotDeps,
) -> None:
    # Cas saisie financière : revenu, dépense ponctuelle ou pointage d'une
    # récurrente connue. On NE déclenche pas non plus le store_memory
    # générique (la valeur d'usage est dans la table `expenses`, pas dans
    # la mémoire sémantique).
    if meta["intent"] == "expense" and meta["expense"]["action"]:
        await handle_expense_side_effect(meta, deps)
        return

    # Cas dépôt cognitif : on persiste dans la table `thoughts` (listing
    # chronologique, état) et on indexe en parallèle dans ChromaDB avec
    # le tag `kind=depot` (préparation à la future détection de boucles).
    # On NE déclenche PAS le store_memory générique sur ce chemin : un
    # dépôt n'est pas un fait stable à apprendre sur l'utilisateur.
    if meta["intent"] == "depot" and meta["depot"]["content"]:
        thought = await deps.thoughts.create(
            content=meta["depot"]["content"],
            kind=meta["depot"]["kind"],
        )
        log.info(
            "thought_stored",
            thought_id=thought.id,
            kind=thought.kind,
            preview=thought.content[:80],
        )
        try:
            await deps.memory.store_depot(
                content=thought.content,
                thought_id=thought.id,
                thought_kind=thought.kind,
            )
        except Exception as exc:
            # SQLite est la source de vérité, ChromaDB est best-effort.
            log.warning("depot_chroma_indexing_failed", error=str(exc))
        return

    if meta["store_memory"] and meta["memory_content"]:
        await deps.memory.store(
            original_message=user_text,
            memory_content=meta["memory_content"],
        )

    if meta["intent"] == "task" and meta["task"]["content"]:
        due_dt = parse_due(meta["task"]["due_str"], deps.settings.timezone)
        task = await deps.tasks.create(content=meta["task"]["content"], due_at=due_dt)
        log.info(
            "task_created",
            task_id=task.id,
            due_str=meta["task"]["due_str"],
            due_at=due_dt.isoformat() if due_dt else None,
        )
        if due_dt is not None:
            deps.scheduler.add_reminder(
                task_id=task.id,
                due_at=due_dt,
                content=task.content,
            )


async def handle_expense_side_effect(meta: Meta, deps: BotDeps) -> None:
    """Persiste une saisie financière (spend / income / tick_recurring).

    Aucune réponse texte n'est produite ici : c'est le LLM qui renvoie un
    ack court (« Noté. ») qui restera tel quel dans `text`. Cette fonction
    se contente d'écrire dans SQLite et de logger.

    Une saisie avec un `recurring_key` inconnu (i.e. absent du YAML) tombe
    en silent no-op : le LLM aurait dû router vers `action=spend` mais on
    refuse d'inventer une récurrente que l'utilisateur n'a pas déclarée.

    Idempotence : si la récurrente est déjà pointée ce mois (cron + /ask
    qui se chevauchent), on log et on skip — pas de double tick.
    """
    em = meta["expense"]
    action = em["action"]
    if action is None:
        return

    when = parse_when_to_date(em["when"], deps.settings.timezone)
    label = em["label"] or "Saisie"
    override_cents = euros_to_cents(em["amount"])

    if action == "spend":
        if override_cents is None:
            log.warning("expense_skipped_invalid_amount", action=action, amount=em["amount"])
            return
        expense = await deps.expenses.add_punctual(
            amount_cents=override_cents,
            label=label,
            category=em["category"],
            occurred_on=when,
            shared=em["shared"],
        )
        log.info(
            "expense_spend_recorded",
            expense_id=expense.id,
            amount_cents=override_cents,
            label=label,
            shared=em["shared"],
        )
        return

    if action == "income":
        # « salaire reçu » : ce jour ancre un nouveau cycle budgétaire, même
        # si l'utilisateur n'a pas précisé de montant (« j'ai reçu mon
        # salaire » sans chiffre). On démarre donc le cycle AVANT de tenter
        # d'enregistrer le revenu.
        if em["starts_cycle"]:
            cycle = await deps.expenses.start_cycle(when)
            log.info("budget_cycle_started", cycle_id=cycle.id, started_on=when.isoformat())
        if override_cents is None:
            if not em["starts_cycle"]:
                log.warning("expense_skipped_invalid_amount", action=action, amount=em["amount"])
            return
        expense = await deps.expenses.add_income(
            amount_cents=override_cents,
            label=label,
            occurred_on=when,
        )
        log.info(
            "expense_income_recorded",
            expense_id=expense.id,
            amount_cents=override_cents,
            label=label,
            starts_cycle=em["starts_cycle"],
        )
        return

    if action == "tick_recurring":
        key = em["recurring_key"]
        if not key:
            log.warning("expense_tick_missing_recurring_key")
            return
        # Import local pour éviter la boucle (config lit le YAML déjà parsé).
        from bot.finance.config import extract_finance_config

        try:
            cfg = extract_finance_config(deps.profile.data)
        except Exception as exc:
            log.warning("expense_tick_finance_config_failed", error=str(exc))
            return
        item = cfg.find(key)
        if item is None:
            log.warning("expense_tick_unknown_key", key=key)
            return
        # Montant : par défaut on prend l'amount du YAML (source de vérité
        # pour un loyer fixe). Si l'utilisateur précise un autre montant
        # ("j'ai versé 11€ sur le PEL"), le LLM le met dans `expense.amount`
        # et on l'utilise comme override. Couvre les placements variables
        # autant que les ponctuelles qui dérapent d'un mois sur l'autre.
        amount_cents = override_cents if override_cents is not None else item.amount_cents
        # `tick_recurring_once` est atomique (check + insert sous lock) :
        # deux requêtes concurrentes ne peuvent pas doubler le pointage.
        tick = await deps.expenses.tick_recurring_once(
            recurring_key=item.key,
            label=item.label,
            amount_cents=amount_cents,
            kind=item.kind,
            occurred_on=when,
            category=item.category,
        )
        if tick is None:
            log.info("expense_tick_duplicate_ignored", key=item.key)
            return
        log.info(
            "expense_recurring_ticked",
            expense_id=tick.id,
            key=item.key,
            kind=item.kind,
            amount_cents=amount_cents,
            override=override_cents is not None,
        )


def euros_to_cents(amount_eur: float | None) -> int | None:
    """Convertit un montant en euros (float) vers des centimes (int).

    Retourne `None` si l'entrée est invalide (négative, nulle ou absente) —
    laisse l'appelant décider quoi faire (skip pour spend/income, fallback
    YAML pour tick_recurring).
    """
    if amount_eur is None or amount_eur <= 0:
        return None
    return round(amount_eur * 100)


async def safe_pending_recurring(deps: BotDeps) -> Sequence[PendingRecurring]:
    """Calcule les récurrentes en attente pour injection dans le system prompt.

    Lit le YAML (`finances.recurring`), liste les écritures du mois courant,
    et retourne les récurrentes non-pointées. Toute erreur (YAML mal formé,
    SQLite indisponible) est avalée : on préfère un prompt sans la section
    qu'un crash sur chaque requête.
    """
    try:
        from bot.finance.budget import compute_budget
        from bot.finance.config import extract_finance_config

        cfg = extract_finance_config(deps.profile.data)
        if not cfg.is_configured:
            return ()
        tz = ZoneInfo(deps.settings.timezone)
        today_d = datetime.now(tz).date()
        cycle_start, cycle_end = await deps.expenses.current_cycle_bounds(today_d)
        cycle_rows = await deps.expenses.list_for_cycle(today_d)
        summary = compute_budget(
            config=cfg,
            month_expenses=cycle_rows,
            year_savings=(),  # inutile pour les pending
            today=today_d,
            cycle_start=cycle_start,
            cycle_end=cycle_end,
        )
        return summary.pending_recurring
    except Exception as exc:
        log.warning("pending_recurring_skipped", error=str(exc))
        return ()
