"""Handler message entrant principal — orchestre LLM + mémoire + tâches + recherche + RSS."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import dateparser

from bot.llm.parser import Meta, MetaParseError, extract_meta
from bot.llm.prompt import build_system_prompt
from bot.logging_conf import get_logger
from bot.rss.manager import FeedAlreadyExists
from bot.security import is_allowed

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

    from bot.config import Settings
    from bot.llm.client import LLMClient
    from bot.memory.manager import MemoryManager
    from bot.rss.fetcher import FeedItem, RssFetcher
    from bot.rss.manager import FeedManager
    from bot.rss.models import Feed
    from bot.search.searxng import SearxngClient
    from bot.tasks.manager import TaskManager
    from bot.tasks.scheduler import ReminderScheduler

log = get_logger(__name__)

MAX_HISTORY = 6
FALLBACK_TEXT = (
    "J'ai eu un souci pour interpréter la réponse, mais je suis là. Redis-moi ça autrement ?"
)


@dataclass
class BotDeps:
    """Conteneur pour toutes les dépendances injectées dans le handler."""

    settings: Settings
    llm: LLMClient
    memory: MemoryManager
    tasks: TaskManager
    scheduler: ReminderScheduler
    search: SearxngClient
    rss: FeedManager
    rss_fetcher: RssFetcher
    history: deque[str]


HandlerFn = Callable[["Update", "ContextTypes.DEFAULT_TYPE"], Coroutine[Any, Any, None]]


def make_handler(deps: BotDeps) -> HandlerFn:
    """Retourne la coroutine handler à enregistrer dans python-telegram-bot."""

    async def handle_message(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if not is_allowed(update, deps.settings.allowed_user_id):
            return

        message = update.message
        if message is None or message.text is None:
            return

        user_text = message.text
        chat_id = message.chat_id
        log.info("message_received", chat_id=chat_id, preview=user_text[:80])

        try:
            reply = await _process(user_text, chat_id, deps)
        except Exception as exc:
            log.exception("handler_failed", error=str(exc))
            reply = "Désolé, une erreur interne est survenue."

        await message.reply_text(reply)

    return handle_message


async def _process(user_text: str, chat_id: int, deps: BotDeps) -> str:
    memory_context = await deps.memory.retrieve_context(user_text, top_k=5)
    system_prompt = build_system_prompt(
        memory_context=memory_context,
        recent_history=list(deps.history),
    )

    raw = await deps.llm.call(system=system_prompt, user=user_text)

    try:
        text, meta = extract_meta(raw)
    except MetaParseError as exc:
        log.warning("meta_parse_failed", error=str(exc), raw_preview=raw[:200])
        return FALLBACK_TEXT

    await _apply_side_effects(user_text, chat_id, meta, deps)

    if meta["intent"] == "search" and meta["search_query"]:
        results = await deps.search.search(meta["search_query"])
        log.info("search_performed", query=meta["search_query"], hits=len(results))
        text = await deps.llm.call_with_search(user_text, results)

    elif meta["intent"] == "feed" and meta["feed"]["action"]:
        text = await _handle_feed(user_text, meta, deps, intro=text)

    deps.history.append(f"user: {user_text}")
    deps.history.append(f"assistant: {text}")
    while len(deps.history) > MAX_HISTORY:
        deps.history.popleft()

    return text


async def _apply_side_effects(
    user_text: str,
    chat_id: int,
    meta: Meta,
    deps: BotDeps,
) -> None:
    if meta["store_memory"] and meta["memory_content"]:
        await deps.memory.store(
            original_message=user_text,
            memory_content=meta["memory_content"],
        )

    if meta["intent"] == "task" and meta["task"]["content"]:
        due_dt = _parse_due(meta["task"]["due_str"], deps.settings.timezone)
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
                chat_id=chat_id,
                content=task.content,
            )


async def _handle_feed(user_text: str, meta: Meta, deps: BotDeps, intro: str) -> str:
    action = meta["feed"]["action"]
    name = meta["feed"]["name"]
    url = meta["feed"]["url"]
    log.info("feed_action", action=action, name=name, url=url)

    if action == "add":
        if not name or not url:
            return "Il me faut un nom et une URL pour ajouter un flux."
        try:
            feed = await deps.rss.add(url=url, name=name)
        except FeedAlreadyExists:
            return f"Le flux « {name} » existe déjà."
        return f"Flux ajouté : {feed.name} ({feed.url})"

    if action == "list":
        feeds = await deps.rss.list(enabled_only=False)
        if not feeds:
            return "Aucun flux enregistré."
        lines = [
            f"- {f.name} [{f.category}] {'✓' if f.enabled else '✗'} — {f.url}"
            for f in feeds
        ]
        return "Tes flux :\n" + "\n".join(lines)

    if action == "remove":
        if not name:
            return "Dis-moi quel flux supprimer."
        ok = await deps.rss.remove(name)
        return f"Flux « {name} » supprimé." if ok else f"Aucun flux trouvé avec le nom « {name} »."

    if action == "summarize":
        target_feeds: Sequence[Feed]
        if name:
            single = await deps.rss.get(name)
            if single is None:
                return f"Aucun flux trouvé pour « {name} »."
            target_feeds = [single]
        else:
            target_feeds = await deps.rss.list(enabled_only=True)
            if not target_feeds:
                return "Aucun flux actif à résumer."

        items = await deps.rss_fetcher.fetch_many(target_feeds, per_feed=5)
        if not items:
            return "Aucun article récupéré pour le moment."
        summary = await _summarize_feed_items(deps, user_text, items[:10])
        return summary if intro.strip() in ("", FALLBACK_TEXT) else f"{intro}\n\n{summary}"

    return intro


async def _summarize_feed_items(
    deps: BotDeps, user_text: str, items: Sequence[FeedItem]
) -> str:
    bullets = "\n".join(
        f"- [{it.feed_name}] {it.title} ({it.url})\n  {it.summary[:300]}"
        for it in items
    )
    system = (
        "Tu es l'assistant personnel d'Arnaud. Tu reçois une liste d'articles RSS récents. "
        "Résume-les en français : 1 à 2 lignes par article, en citant le flux source et l'URL. "
        "Sois factuel et concis. N'inclus PAS de bloc <meta>."
    )
    user = f"Question initiale : {user_text}\n\nArticles :\n{bullets}"
    return await deps.llm.chat(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )


def _parse_due(due_str: str | None, tz_name: str) -> datetime | None:
    """Parse une expression FR et retourne un datetime aware dans la timezone voulue.

    Sans `TIMEZONE` + `RETURN_AS_TIMEZONE_AWARE`, dateparser renvoie un datetime
    naïf, qu'APScheduler interprète en UTC → décalage en prod (le container est
    souvent en UTC).
    """
    if not due_str:
        return None
    parsed = dateparser.parse(
        due_str,
        languages=["fr"],
        settings={
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": tz_name,
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(tz_name))
    return parsed
