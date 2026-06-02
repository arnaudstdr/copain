"""Pipeline applicatif : mémoire + LLM + routing `<meta>` + side effects.

La couche transport (HTTP via FastAPI dans `bot/api.py`) appelle
`process_message(text, images?)` pour obtenir la réponse texte finale.
Les rappels de tâche écrivent dans la file `pending_notifications` qui sera
consommée par `GET /notifications` côté client iOS.
"""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal, TypedDict
from zoneinfo import ZoneInfo

from bot.llm.parser import Meta, MetaParseError, MetaStreamFilter, extract_meta
from bot.llm.prompt import build_system_prompt
from bot.logging_conf import get_logger
from bot.pipeline.dates import parse_due, parse_when_to_date
from bot.pipeline.handlers import FALLBACK_TEXT, run_intent_handler

if TYPE_CHECKING:
    from bot.calendar.client import ICloudCalendarClient
    from bot.config import Settings
    from bot.finance.budget import PendingRecurring
    from bot.finance.manager import ExpenseManager
    from bot.fuel.client import FuelClient
    from bot.fuel.geocoding import NominatimClient
    from bot.llm.client import LLMClient
    from bot.locations.store import LocationEventStore
    from bot.memory.manager import MemoryManager
    from bot.news.client import NewsCurator
    from bot.proactivity.service import ProactivityService
    from bot.profile import UserProfile
    from bot.rss.fetcher import RssFetcher
    from bot.rss.manager import FeedManager
    from bot.search.searxng import SearxngClient
    from bot.tasks.manager import TaskManager
    from bot.tasks.scheduler import ReminderScheduler
    from bot.thoughts.manager import ThoughtManager
    from bot.weather.client import OpenMeteoClient

log = get_logger(__name__)

MAX_HISTORY = 6

# Meta neutre renvoyé quand le bloc <meta> est absent / invalide. Intent
# "answer" + tous les sous-objets vides : aucun side effect n'est déclenché
# et le client (API) considère qu'aucune card ne doit être rafraîchie.
_FALLBACK_META: Meta = {
    "intent": "answer",
    "store_memory": False,
    "memory_content": None,
    "task": {"content": None, "due_str": None},
    "feed": {"action": None, "name": None, "url": None},
    "event": {
        "action": None,
        "title": None,
        "start_str": None,
        "end_str": None,
        "location": None,
        "description": None,
        "range_str": None,
        "calendar_name": None,
    },
    "fuel": {"fuel_type": None, "radius_km": None, "location": None},
    "weather": {"location": None, "when": None},
    "depot": {"content": None, "kind": None},
    "expense": {
        "action": None,
        "amount": None,
        "label": None,
        "category": None,
        "recurring_key": None,
        "when": None,
        "shared": False,
        "starts_cycle": False,
    },
    "search_query": None,
}


class StreamEvent(TypedDict, total=False):
    """Événement émis par `process_message_stream`, sérialisé en SSE par l'API.

    - `delta`   : chunk de texte visible à concaténer côté client.
    - `replace` : le texte remplace tout ce qui a été affiché (handlers Python,
      fallback quand le bloc <meta> est invalide, reset avant le résumé search).
    - `done`    : fin de réponse ; porte la `Meta` pour que l'API calcule les
      `refresh_cards`.
    """

    type: Literal["delta", "replace", "done"]
    text: str
    meta: Meta


@dataclass
class BotDeps:
    """Conteneur pour toutes les dépendances injectées dans le pipeline."""

    settings: Settings
    llm: LLMClient
    memory: MemoryManager
    tasks: TaskManager
    thoughts: ThoughtManager
    expenses: ExpenseManager
    scheduler: ReminderScheduler
    search: SearxngClient
    rss: FeedManager
    rss_fetcher: RssFetcher
    calendar: ICloudCalendarClient
    fuel: FuelClient
    geocoder: NominatimClient
    weather: OpenMeteoClient
    news: NewsCurator
    profile: UserProfile
    location_events: LocationEventStore
    proactivity: ProactivityService
    history: deque[str]


async def process_message(
    user_text: str,
    deps: BotDeps,
    images: list[bytes] | None = None,
    voice_mode: bool = False,
) -> tuple[str, Meta]:
    """Point d'entrée unique appelé par la couche transport (`bot/api.py`).

    Retourne la réponse complète (pas de streaming) **et** le bloc `Meta`
    extrait du LLM, afin que l'API puisse signaler au front quelles cards
    rafraîchir. Si le bloc <meta> est absent / invalide, on renvoie
    `(FALLBACK_TEXT, _FALLBACK_META)` (intent="answer", aucun refresh).

    Quand `voice_mode=True` (raccourci Siri via header X-Source: siri),
    le system prompt reçoit un préambule TTS-friendly pour produire des
    réponses très courtes et lisibles à voix haute.

    Les rappels créés en chemin par `_apply_side_effects` écrivent dans
    `pending_notifications` via `ReminderScheduler.add_reminder`.
    """
    system_prompt = await _build_prompt(user_text, deps, voice_mode=voice_mode)

    user_content = (
        user_text if user_text else "Analyse cette image et propose une action pertinente."
    )
    raw = await deps.llm.call(system=system_prompt, user=user_content, images=images)

    try:
        text, meta = extract_meta(raw)
    except MetaParseError as exc:
        log.warning("meta_parse_failed", error=str(exc), raw_preview=raw[:200])
        return FALLBACK_TEXT, _FALLBACK_META

    await _apply_side_effects(user_text, meta, deps)

    if meta["intent"] == "search" and meta["search_query"]:
        results = await deps.search.search(meta["search_query"])
        log.info("search_performed", query=meta["search_query"], hits=len(results))
        text = await deps.llm.call_with_search(user_text, results)
    else:
        replacement = await run_intent_handler(user_text, meta, deps, intro=text)
        if replacement is not None:
            text = replacement

    history_user = user_text if user_text else "(image envoyée)"
    if images:
        history_user = f"[photo] {history_user}"
    deps.history.append(f"user: {history_user}")
    deps.history.append(f"assistant: {text}")

    return text, meta


async def process_message_stream(
    user_text: str,
    deps: BotDeps,
) -> AsyncIterator[StreamEvent]:
    """Variante streamée de `process_message`, consommée par `POST /ask/stream`.

    Yield des `StreamEvent` au fil de l'eau : les `delta` sont le texte visible
    du LLM débarrassé du bloc <meta> (via `MetaStreamFilter`), le `done` final
    porte la `Meta` pour le calcul des `refresh_cards` côté API.

    L'intent n'est connu qu'à la fin du premier appel LLM (le bloc <meta>
    arrive en dernier). Les intents dont le texte final est produit par un
    handler Python (feed/event/fuel/weather) émettent donc un `replace` après
    les deltas de l'intro ; `search` émet `replace("")` puis streame le résumé
    du second appel LLM (le plus long — c'est là que le streaming paie).

    Chemin texte uniquement : pas d'images (les photos restent sur
    `POST /ask/image`) ni de voice_mode (Siri reste sur `POST /ask`).
    """
    system_prompt = await _build_prompt(user_text, deps, voice_mode=False)

    meta_filter = MetaStreamFilter()
    emitted_any = False
    async for chunk in deps.llm.chat_stream(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ]
    ):
        visible = meta_filter.feed(chunk)
        if not emitted_any:
            visible = visible.lstrip()  # aligne le début sur le .strip() d'extract_meta
        if visible:
            emitted_any = True
            yield {"type": "delta", "text": visible}
    tail = meta_filter.flush()
    if tail:
        yield {"type": "delta", "text": tail}

    try:
        text, meta = extract_meta(meta_filter.raw)
    except MetaParseError as exc:
        log.warning("meta_parse_failed", error=str(exc), raw_preview=meta_filter.raw[:200])
        # Même comportement que process_message : texte de secours, meta
        # neutre, pas de side effects ni d'entrée dans l'history.
        yield {"type": "replace", "text": FALLBACK_TEXT}
        yield {"type": "done", "meta": _FALLBACK_META}
        return

    await _apply_side_effects(user_text, meta, deps)

    if meta["intent"] == "search" and meta["search_query"]:
        results = await deps.search.search(meta["search_query"])
        log.info("search_performed", query=meta["search_query"], hits=len(results))
        yield {"type": "replace", "text": ""}
        summary_parts: list[str] = []
        async for piece in deps.llm.call_with_search_stream(user_text, results):
            summary_parts.append(piece)
            yield {"type": "delta", "text": piece}
        text = "".join(summary_parts)
    else:
        replacement = await run_intent_handler(user_text, meta, deps, intro=text)
        if replacement is not None:
            text = replacement
            yield {"type": "replace", "text": text}

    deps.history.append(f"user: {user_text}")
    deps.history.append(f"assistant: {text}")

    yield {"type": "done", "meta": meta}


async def _build_prompt(user_text: str, deps: BotDeps, voice_mode: bool) -> str:
    """Prépare le system prompt complet (mémoire RAG, localisation, pending récurrentes).

    Partagé entre `process_message` et `process_message_stream`.
    """
    memory_context = await deps.memory.retrieve_context(
        user_text or "(image envoyée sans légende)", top_k=5
    )
    tz = ZoneInfo(deps.settings.timezone)
    now_str = datetime.now(tz).strftime("%A %d %B %Y à %H:%M")
    current_location = await deps.location_events.get_current_location()
    pending_recurring = await _safe_pending_recurring(deps)
    return build_system_prompt(
        memory_context=memory_context,
        recent_history=list(deps.history),
        current_datetime=now_str,
        home_city=deps.settings.home_city,
        user_profile=deps.profile,
        voice_mode=voice_mode,
        current_location=current_location,
        timezone=deps.settings.timezone,
        pending_recurring=pending_recurring,
    )


async def _apply_side_effects(
    user_text: str,
    meta: Meta,
    deps: BotDeps,
) -> None:
    # Cas saisie financière : revenu, dépense ponctuelle ou pointage d'une
    # récurrente connue. On NE déclenche pas non plus le store_memory
    # générique (la valeur d'usage est dans la table `expenses`, pas dans
    # la mémoire sémantique).
    if meta["intent"] == "expense" and meta["expense"]["action"]:
        await _handle_expense_side_effect(meta, deps)
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


async def _handle_expense_side_effect(meta: Meta, deps: BotDeps) -> None:
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
    override_cents = _euros_to_cents(em["amount"])

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


def _euros_to_cents(amount_eur: float | None) -> int | None:
    """Convertit un montant en euros (float) vers des centimes (int).

    Retourne `None` si l'entrée est invalide (négative, nulle ou absente) —
    laisse l'appelant décider quoi faire (skip pour spend/income, fallback
    YAML pour tick_recurring).
    """
    if amount_eur is None or amount_eur <= 0:
        return None
    return round(amount_eur * 100)


async def _safe_pending_recurring(deps: BotDeps) -> Sequence[PendingRecurring]:
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
