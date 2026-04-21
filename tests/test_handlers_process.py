"""Tests d'intégration du pipeline `_process` avec toutes les dépendances mockées.

Objectif : vérifier l'orchestration LLM → parser → side_effects → scheduler.
C'est le cœur du bot, les autres tests ne couvraient que chaque brique isolée.
"""

from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.handlers import BotDeps, _process


def _meta_block(
    intent: str = "answer",
    *,
    store_memory: bool = False,
    memory_content: str | None = None,
    task_content: str | None = None,
    task_due: str | None = None,
    feed_action: str | None = None,
    feed_name: str | None = None,
    feed_url: str | None = None,
    event_action: str | None = None,
    event_title: str | None = None,
    event_start: str | None = None,
    event_end: str | None = None,
    event_location: str | None = None,
    event_calendar: str | None = None,
    event_range: str | None = None,
    search_query: str | None = None,
) -> str:
    """Construit une réponse LLM factice avec bloc <meta> valide."""
    import json

    meta = {
        "intent": intent,
        "store_memory": store_memory,
        "memory_content": memory_content,
        "task": {"content": task_content, "due_str": task_due},
        "feed": {"action": feed_action, "name": feed_name, "url": feed_url},
        "event": {
            "action": event_action,
            "title": event_title,
            "start_str": event_start,
            "end_str": event_end,
            "location": event_location,
            "description": None,
            "range_str": event_range,
            "calendar_name": event_calendar,
        },
        "search_query": search_query,
    }
    return f"Réponse texte.\n<meta>{json.dumps(meta)}</meta>"


@pytest.fixture
def deps() -> BotDeps:
    """BotDeps entièrement mocké pour isoler _process des vraies dépendances."""
    settings = MagicMock()
    settings.allowed_user_id = 42
    settings.timezone = "Europe/Paris"

    memory = MagicMock()
    memory.retrieve_context = AsyncMock(return_value=[])
    memory.store = AsyncMock()

    llm = MagicMock()
    llm.call = AsyncMock(return_value=_meta_block(intent="answer"))
    llm.call_with_search = AsyncMock(return_value="Résumé de la recherche")
    llm.chat = AsyncMock(return_value="Résumé des articles")

    tasks = MagicMock()
    fake_task = MagicMock()
    fake_task.id = 123
    fake_task.content = "acheter du pain"
    tasks.create = AsyncMock(return_value=fake_task)

    scheduler = MagicMock()
    search = MagicMock()
    search.search = AsyncMock(return_value=[])
    rss = MagicMock()
    rss_fetcher = MagicMock()
    briefing = MagicMock()
    calendar = MagicMock()

    return BotDeps(
        settings=settings,
        llm=llm,
        memory=memory,
        tasks=tasks,
        scheduler=scheduler,
        search=search,
        rss=rss,
        rss_fetcher=rss_fetcher,
        briefing=briefing,
        calendar=calendar,
        history=deque(maxlen=6),
    )


async def test_process_answer_intent_returns_text(deps: BotDeps) -> None:
    text = await _process("salut", chat_id=42, deps=deps)
    assert text == "Réponse texte."
    deps.memory.store.assert_not_called()
    deps.tasks.create.assert_not_called()
    deps.scheduler.add_reminder.assert_not_called()


async def test_process_stores_memory_when_flagged(deps: BotDeps) -> None:
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="answer", store_memory=True, memory_content="Arnaud habite Sélestat"
        )
    )
    await _process("j'habite Sélestat", chat_id=42, deps=deps)
    deps.memory.store.assert_awaited_once_with(
        original_message="j'habite Sélestat",
        memory_content="Arnaud habite Sélestat",
    )


async def test_process_task_intent_creates_task_and_schedules_reminder(
    deps: BotDeps,
) -> None:
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="task",
            task_content="acheter du pain",
            task_due="demain 18:00",
        )
    )
    await _process("rappelle-moi d'acheter du pain demain 18h", chat_id=42, deps=deps)
    deps.tasks.create.assert_awaited_once()
    deps.scheduler.add_reminder.assert_called_once()
    call = deps.scheduler.add_reminder.call_args
    assert call.kwargs["task_id"] == 123
    assert call.kwargs["chat_id"] == 42
    assert call.kwargs["content"] == "acheter du pain"
    assert call.kwargs["due_at"].hour == 18


async def test_process_task_without_due_skips_reminder(deps: BotDeps) -> None:
    deps.llm.call = AsyncMock(
        return_value=_meta_block(intent="task", task_content="ranger le bureau", task_due=None)
    )
    await _process("je dois ranger mon bureau", chat_id=42, deps=deps)
    deps.tasks.create.assert_awaited_once()
    deps.scheduler.add_reminder.assert_not_called()


async def test_process_search_intent_relaunches_llm_with_results(
    deps: BotDeps,
) -> None:
    deps.llm.call = AsyncMock(
        return_value=_meta_block(intent="search", search_query="météo Paris demain")
    )
    deps.search.search = AsyncMock(return_value=[{"title": "T", "url": "u", "snippet": "s"}])
    text = await _process("il fera quel temps demain ?", chat_id=42, deps=deps)
    deps.search.search.assert_awaited_once_with("météo Paris demain")
    deps.llm.call_with_search.assert_awaited_once()
    assert text == "Résumé de la recherche"


async def test_process_feed_list_returns_formatted_list(deps: BotDeps) -> None:
    deps.llm.call = AsyncMock(return_value=_meta_block(intent="feed", feed_action="list"))
    deps.rss.list = AsyncMock(return_value=[])
    text = await _process("mes flux ?", chat_id=42, deps=deps)
    assert "Aucun flux enregistré" in text


async def test_process_event_create_calls_calendar(deps: BotDeps) -> None:
    fake_event = MagicMock()
    fake_event.title = "RDV dentiste"
    fake_event.calendar_name = "Personnel"
    from datetime import UTC, datetime

    fake_event.start = datetime(2026, 4, 22, 15, 0, tzinfo=UTC)
    deps.calendar.is_connected = True
    deps.calendar.create_event = AsyncMock(return_value=fake_event)
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="event",
            event_action="create",
            event_title="RDV dentiste",
            event_start="mardi 15h",
        )
    )
    text = await _process("mets un RDV dentiste mardi 15h", chat_id=42, deps=deps)
    deps.calendar.create_event.assert_awaited_once()
    assert "RDV dentiste" in text


async def test_process_meta_parse_failure_returns_fallback(deps: BotDeps) -> None:
    deps.llm.call = AsyncMock(return_value="Pas de bloc meta ici.")
    text = await _process("blabla", chat_id=42, deps=deps)
    from bot.handlers import FALLBACK_TEXT

    assert text == FALLBACK_TEXT


async def test_process_updates_history_with_user_and_assistant(deps: BotDeps) -> None:
    await _process("salut", chat_id=42, deps=deps)
    await _process("ça va", chat_id=42, deps=deps)
    assert len(deps.history) == 4
    assert deps.history[0].startswith("user: salut")
    assert deps.history[1].startswith("assistant:")
    assert deps.history[2].startswith("user: ça va")


async def test_process_history_is_bounded_by_maxlen(deps: BotDeps) -> None:
    """10 échanges → 20 entrées → deque tronqué à 6 (MAX_HISTORY)."""
    for i in range(10):
        await _process(f"msg {i}", chat_id=42, deps=deps)
    assert len(deps.history) == 6
    # Les dernières entrées sont conservées, les plus anciennes purgées.
    assert deps.history[-2].startswith("user: msg 9")


async def test_process_with_image_prepends_photo_tag_in_history(
    deps: BotDeps,
) -> None:
    await _process("décris", chat_id=42, deps=deps, images=[b"fakepng"])
    assert deps.history[0].startswith("user: [photo] décris")


async def test_process_empty_text_with_image_uses_default_prompt(
    deps: BotDeps,
) -> None:
    await _process("", chat_id=42, deps=deps, images=[b"fakepng"])
    # Vérifie que le LLM a bien reçu le prompt par défaut (pas une string vide)
    call_kwargs = deps.llm.call.call_args.kwargs
    assert "Analyse cette image" in call_kwargs["user"]
