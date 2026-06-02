"""Pipeline applicatif : mémoire + LLM + routing `<meta>` + side effects.

La couche transport (HTTP via FastAPI dans `bot/api.py`) appelle
`process_message(text, images?)` pour obtenir la réponse texte finale.
Les rappels de tâche écrivent dans la file `pending_notifications` qui sera
consommée par `GET /notifications` côté client iOS.
"""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal, TypedDict
from zoneinfo import ZoneInfo

from bot.llm.parser import Meta, MetaParseError, MetaStreamFilter, extract_meta
from bot.llm.prompt import build_system_prompt
from bot.logging_conf import get_logger
from bot.pipeline.handlers import FALLBACK_TEXT, run_intent_handler
from bot.pipeline.side_effects import (
    apply_side_effects,
    safe_open_worries,
    safe_pending_recurring,
)

if TYPE_CHECKING:
    from bot.calendar.client import ICloudCalendarClient
    from bot.config import Settings
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
    from bot.search.searxng import SearchResult, SearxngClient
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
    "depot": {"content": None, "kind": None, "action": "add", "thought_id": None},
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


@dataclass
class _RouteOutcome:
    """Issue du routing commun : exactement une branche est active.

    - `search_results` non-None → intent search, l'orchestrateur produit le
      résumé à partir de ces résultats (variante streamée ou non) ;
    - sinon `replacement` porte l'éventuel texte final d'un handler Python
      (None = l'intro du LLM reste le texte final).

    `loop_size` est orthogonal aux deux branches : taille de la boucle de
    rumination rejointe par un dépôt (None sinon), suffixée à l'accusé par
    template (décision D3 — jamais par second appel LLM).
    """

    search_results: list[SearchResult] | None = None
    replacement: str | None = None
    loop_size: int | None = None


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

    Les rappels créés en chemin par `apply_side_effects` écrivent dans
    `pending_notifications` via `ReminderScheduler.add_reminder`.
    """
    system_prompt = await _build_prompt(user_text, deps, voice_mode=voice_mode)

    user_content = (
        user_text if user_text else "Analyse cette image et propose une action pertinente."
    )
    raw = await deps.llm.call(system=system_prompt, user=user_content, images=images)

    extracted = _try_extract_meta(raw)
    if extracted is None:
        return FALLBACK_TEXT, _FALLBACK_META
    text, meta = extracted

    outcome = await _route_and_apply(user_text, meta, deps, intro=text)
    if outcome.search_results is not None:
        text = await deps.llm.call_with_search(user_text, outcome.search_results)
    elif outcome.replacement is not None:
        text = outcome.replacement
    if outcome.loop_size is not None:
        text += _loop_suffix(outcome.loop_size)

    history_user = user_text if user_text else "(image envoyée)"
    if images:
        history_user = f"[photo] {history_user}"
    _record_history(deps, history_user, text)

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

    extracted = _try_extract_meta(meta_filter.raw)
    if extracted is None:
        # Même comportement que process_message : texte de secours, meta
        # neutre, pas de side effects ni d'entrée dans l'history.
        yield {"type": "replace", "text": FALLBACK_TEXT}
        yield {"type": "done", "meta": _FALLBACK_META}
        return
    text, meta = extracted

    outcome = await _route_and_apply(user_text, meta, deps, intro=text)
    if outcome.search_results is not None:
        yield {"type": "replace", "text": ""}
        summary_parts: list[str] = []
        async for piece in deps.llm.call_with_search_stream(user_text, outcome.search_results):
            summary_parts.append(piece)
            yield {"type": "delta", "text": piece}
        text = "".join(summary_parts)
    elif outcome.replacement is not None:
        text = outcome.replacement
        yield {"type": "replace", "text": text}
    if outcome.loop_size is not None:
        # Suffixe boucle : frame delta supplémentaire après l'accusé (D3).
        suffix = _loop_suffix(outcome.loop_size)
        text += suffix
        yield {"type": "delta", "text": suffix}

    _record_history(deps, user_text, text)

    yield {"type": "done", "meta": meta}


def _try_extract_meta(raw: str) -> tuple[str, Meta] | None:
    """Extrait `(texte, meta)` de la réponse LLM, ou None si le bloc est invalide.

    Le None signifie pour l'orchestrateur appelant : texte de secours, meta
    neutre, et surtout NI side effects NI entrée dans l'history (comportement
    verrouillé par les tests des deux chemins).
    """
    try:
        return extract_meta(raw)
    except MetaParseError as exc:
        log.warning("meta_parse_failed", error=str(exc), raw_preview=raw[:200])
        return None


async def _route_and_apply(user_text: str, meta: Meta, deps: BotDeps, intro: str) -> _RouteOutcome:
    """Séquence métier commune aux deux orchestrateurs : side effects puis routing.

    Pour l'intent search (avec query), exécute la recherche SearXNG et laisse
    l'orchestrateur produire le résumé via le second appel LLM ; pour les
    autres intents, délègue aux handlers Python qui peuvent remplacer l'intro.
    """
    effects = await apply_side_effects(user_text, meta, deps)
    if effects.replace_text is not None:
        # Les side effects imposent le texte final (clôture NL avec id
        # invalide → réponse honnête) : pas de handler, pas de search.
        return _RouteOutcome(replacement=effects.replace_text, loop_size=effects.loop_size)
    if meta["intent"] == "search" and meta["search_query"]:
        results = await deps.search.search(meta["search_query"])
        log.info("search_performed", query=meta["search_query"], hits=len(results))
        return _RouteOutcome(search_results=results, loop_size=effects.loop_size)
    replacement = await run_intent_handler(user_text, meta, deps, intro=intro)
    return _RouteOutcome(replacement=replacement, loop_size=effects.loop_size)


def _loop_suffix(loop_size: int) -> str:
    """Suffixe template de l'accusé quand le dépôt rejoint une boucle (≥ 3 membres)."""
    return f" — {loop_size}e fois que ça revient."


def _record_history(deps: BotDeps, user_entry: str, assistant_text: str) -> None:
    """Append user/assistant dans l'history roulante, APRÈS le texte final.

    L'ordre compte : le texte enregistré doit être celui réellement renvoyé
    (y compris un remplacement par handler ou un résumé search).
    """
    deps.history.append(f"user: {user_entry}")
    deps.history.append(f"assistant: {assistant_text}")


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
    pending_recurring = await safe_pending_recurring(deps)
    open_worries = await safe_open_worries(deps)
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
        open_worries=open_worries,
    )
