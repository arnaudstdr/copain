"""Side effects par intent : memory, task + rappel, dépôt cognitif, finances.

Appliqués par les orchestrateurs après extraction du bloc `<meta>`, avant
le dispatch des handlers : on persiste d'abord (SQLite / ChromaDB), le texte
de réponse vient ensuite. `BotDeps` n'est importé que sous TYPE_CHECKING
(`core` importe ce module au runtime, jamais l'inverse).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from bot.logging_conf import get_logger
from bot.pipeline.dates import parse_due, parse_when_to_date
from bot.thoughts.restitution import LOOP_WINDOW_DAYS, is_loop

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bot.finance.budget import PendingRecurring
    from bot.llm.parser import Meta
    from bot.pipeline.core import BotDeps
    from bot.thoughts.models import Thought

log = get_logger(__name__)

# Réponse honnête quand le LLM désigne un `thought_id` invalide (halluciné,
# déjà clos, ou absent) sur une clôture en langage naturel : aucun side
# effect n'a eu lieu, le texte optimiste du LLM doit être remplacé.
CLOSE_NOT_FOUND_TEXT = (
    "Hmm, je n'ai pas retrouvé ce souci dans tes dépôts ouverts — rien n'a été clos."
)

# Nombre max de soucis ouverts injectés dans le system prompt (les plus
# récents) : borne la taille du prompt et le périmètre désignable par le LLM.
OPEN_WORRIES_PROMPT_LIMIT = 10


@dataclass(frozen=True, slots=True)
class SideEffectsOutcome:
    """Issue des side effects, consommée par les orchestrateurs de `core`.

    - `loop_size` : taille de la boucle de rumination que le dépôt vient de
      rejoindre (nouveau dépôt inclus), ou None si pas de boucle / pas un
      dépôt. Sert à suffixer l'accusé par template (décision D3 du SPEC).
    - `replace_text` : texte qui doit remplacer la réponse du LLM (clôture
      NL avec id invalide → réponse honnête), ou None si le texte du LLM
      reste valable.
    """

    loop_size: int | None = None
    replace_text: str | None = None


async def apply_side_effects(
    user_text: str,
    meta: Meta,
    deps: BotDeps,
) -> SideEffectsOutcome:
    # Cas saisie financière : revenu, dépense ponctuelle ou pointage d'une
    # récurrente connue. On NE déclenche pas non plus le store_memory
    # générique (la valeur d'usage est dans la table `expenses`, pas dans
    # la mémoire sémantique).
    if meta["intent"] == "expense" and meta["expense"]["action"]:
        await handle_expense_side_effect(meta, deps)
        return SideEffectsOutcome()

    # Cas clôture en langage naturel (« c'est bon pour X ») : le LLM désigne
    # un id pris dans la section « Soucis ouverts » du prompt. On valide
    # contre les soucis RÉELLEMENT ouverts (un id halluciné ou déjà clos est
    # traité pareil : réponse honnête, aucun side effect). Ce chemin ne passe
    # JAMAIS par store_depot ni par la détection de boucle.
    if meta["intent"] == "depot" and meta["depot"]["action"] == "close":
        return await _close_thought_from_meta(meta, deps)

    # Cas dépôt cognitif : on persiste dans la table `thoughts` (listing
    # chronologique, état) et on indexe en parallèle dans ChromaDB avec
    # le tag `kind=depot` (détection de boucles ci-dessous).
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
        loop_size: int | None = None
        try:
            await deps.memory.store_depot(
                content=thought.content,
                thought_id=thought.id,
                thought_kind=thought.kind,
            )
        except Exception as exc:
            # SQLite est la source de vérité, ChromaDB est best-effort.
            # Sans indexation, pas de recherche de similarité possible.
            log.warning("depot_chroma_indexing_failed", error=str(exc))
        else:
            loop_size = await _detect_depot_loop(thought, deps)
        return SideEffectsOutcome(loop_size=loop_size)

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
    return SideEffectsOutcome()


async def _close_thought_from_meta(meta: Meta, deps: BotDeps) -> SideEffectsOutcome:
    """Clôt le souci désigné par `depot.thought_id` après validation stricte.

    L'id doit appartenir aux soucis ouverts injectés dans le prompt (mêmes
    critères de collecte : kind=worry, les plus récents). Sinon → outcome
    avec `replace_text` honnête, aucun side effect.
    """
    thought_id = meta["depot"]["thought_id"]
    if thought_id is not None:
        open_worries = await deps.thoughts.list_open(
            kinds=["worry"], limit=OPEN_WORRIES_PROMPT_LIMIT
        )
        if any(t.id == thought_id for t in open_worries):
            await deps.thoughts.close(thought_id)
            log.info("thought_closed_nl", thought_id=thought_id)
            return SideEffectsOutcome()
    log.warning("thought_close_nl_invalid_id", thought_id=thought_id)
    return SideEffectsOutcome(replace_text=CLOSE_NOT_FOUND_TEXT)


async def safe_open_worries(deps: BotDeps) -> Sequence[Thought]:
    """Collecte les soucis ouverts pour injection dans le system prompt.

    Fail-soft : SQLite indisponible → liste vide, le prompt se construit
    sans la section (pattern `safe_pending_recurring`).
    """
    try:
        return await deps.thoughts.list_open(kinds=["worry"], limit=OPEN_WORRIES_PROMPT_LIMIT)
    except Exception as exc:
        log.warning("open_worries_skipped", error=str(exc))
        return ()


async def _detect_depot_loop(thought: Thought, deps: BotDeps) -> int | None:
    """Détecte si le dépôt fraîchement indexé rejoint une boucle de rumination.

    Fail-soft intégral : aucune exception de la similarité ChromaDB ou du
    rechargement SQLite ne doit casser l'accusé de réception. La vérité
    d'état (existence, `created_at`) est SQLite : un match ChromaDB dont le
    `thought_id` n'y figure plus est un orphelin, ignoré avec un log debug.
    """
    try:
        matches = await deps.memory.find_similar_depots(
            thought.content,
            top_k=8,
            max_distance=deps.settings.foryou_similarity_max_distance,
        )
        if not matches:
            return None
        now = datetime.now(UTC)
        # Dates SQLite naïves UTC : borne `since` naïve pour la requête,
        # réattache UTC sur les `created_at` pour la fenêtre d'`is_loop`.
        since = (now - timedelta(days=LOOP_WINDOW_DAYS)).replace(tzinfo=None)
        neighbours = await deps.thoughts.list_since(since)
        member_created_ats = {
            t.id: t.created_at.replace(tzinfo=UTC) for t in neighbours if t.id != thought.id
        }
        for match in matches:
            if match.thought_id != thought.id and match.thought_id not in member_created_ats:
                log.debug("depot_match_orphan_sqlite", thought_id=match.thought_id)
        loop_size = is_loop(
            matches,
            new_thought_id=thought.id,
            member_created_ats=member_created_ats,
            now=now,
        )
        if loop_size is not None:
            log.info("depot_loop_detected", thought_id=thought.id, loop_size=loop_size)
        return loop_size
    except Exception as exc:
        log.warning("depot_loop_detection_failed", error=str(exc))
        return None


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
