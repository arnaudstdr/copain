"""Tests du pipeline streamé `process_message_stream` (toutes dépendances mockées).

Vérifie le protocole d'événements delta/replace/done consommé par
`POST /ask/stream` : filtrage du bloc <meta>, side effects, remplacement du
texte par les handlers, streaming du résumé search, fallback meta invalide.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.pipeline import FALLBACK_TEXT, BotDeps, StreamEvent, process_message_stream
from bot.profile import UserProfile


def _meta_block(
    intent: str = "answer",
    *,
    task_content: str | None = None,
    task_due: str | None = None,
    search_query: str | None = None,
    weather_location: str | None = None,
) -> str:
    """Bloc <meta> minimal valide (les sous-objets absents sont tolérés par le parser)."""
    meta = {
        "intent": intent,
        "store_memory": False,
        "memory_content": None,
        "task": {"content": task_content, "due_str": task_due},
        "weather": {"location": weather_location, "when": None},
        "search_query": search_query,
    }
    return f"<meta>{json.dumps(meta)}</meta>"


def _astream(chunks: list[str]) -> AsyncIterator[str]:
    """Fabrique un async iterator de chunks, façon LLMClient.chat_stream."""

    async def gen() -> AsyncIterator[str]:
        for c in chunks:
            yield c

    return gen()


async def _collect(events: AsyncIterator[StreamEvent]) -> list[StreamEvent]:
    return [e async for e in events]


def _visible_text(events: list[StreamEvent]) -> str:
    """Reconstruit ce que verrait l'utilisateur (deltas concaténés, replace écrase)."""
    acc = ""
    for e in events:
        if e["type"] == "delta":
            acc += e.get("text", "")
        elif e["type"] == "replace":
            acc = e.get("text", "")
    return acc


@pytest.fixture
def deps() -> BotDeps:
    """BotDeps mocké, calqué sur la fixture de test_pipeline_process."""
    settings = MagicMock()
    settings.timezone = "Europe/Paris"
    settings.home_lat = 48.26
    settings.home_lon = 7.45
    settings.home_city = "Sélestat"
    settings.fuel_default_radius_km = 10.0

    memory = MagicMock()
    memory.retrieve_context = AsyncMock(return_value=[])
    memory.store = AsyncMock()
    memory.store_depot = AsyncMock()

    llm = MagicMock()
    llm.chat_stream = MagicMock(
        return_value=_astream(["Réponse texte.", f"\n{_meta_block('answer')}"])
    )
    llm.call_with_search_stream = MagicMock(return_value=_astream(["Résumé."]))

    tasks = MagicMock()
    fake_task = MagicMock()
    fake_task.id = 123
    fake_task.content = "acheter du pain"
    tasks.create = AsyncMock(return_value=fake_task)

    search = MagicMock()
    search.search = AsyncMock(return_value=[])

    weather = MagicMock()
    weather.get_forecast = AsyncMock(return_value=[])

    location_events = MagicMock()
    location_events.get_current_location = AsyncMock(return_value=None)

    return BotDeps(
        settings=settings,
        llm=llm,
        memory=memory,
        tasks=tasks,
        thoughts=MagicMock(),
        expenses=MagicMock(),
        scheduler=MagicMock(),
        search=search,
        rss=MagicMock(),
        rss_fetcher=MagicMock(),
        calendar=MagicMock(),
        fuel=MagicMock(),
        geocoder=MagicMock(),
        weather=weather,
        news=MagicMock(),
        profile=UserProfile(raw_yaml="", is_loaded=False),
        location_events=location_events,
        proactivity=MagicMock(),
        history=deque(maxlen=6),
    )


async def test_stream_answer_intent_yields_deltas_then_done(deps: BotDeps) -> None:
    events = await _collect(process_message_stream("salut", deps))
    assert events[-1]["type"] == "done"
    assert events[-1]["meta"]["intent"] == "answer"
    assert _visible_text(events) == "Réponse texte."


async def test_stream_never_emits_meta_block(deps: BotDeps) -> None:
    """Le bloc <meta>, même coupé entre deux chunks, ne fuit jamais dans les deltas."""
    deps.llm.chat_stream = MagicMock(
        return_value=_astream(["Bonjour", " toi.", "\n\n<me", f"ta{_meta_block('answer')[5:]}"])
    )
    events = await _collect(process_message_stream("salut", deps))
    for e in events:
        assert "<meta>" not in e.get("text", "")
    assert _visible_text(events) == "Bonjour toi."


async def test_stream_updates_history_with_final_text(deps: BotDeps) -> None:
    await _collect(process_message_stream("salut", deps))
    assert "user: salut" in deps.history
    assert "assistant: Réponse texte." in deps.history


async def test_stream_task_intent_applies_side_effects(deps: BotDeps) -> None:
    deps.llm.chat_stream = MagicMock(
        return_value=_astream(
            ["C'est noté !\n", _meta_block("task", task_content="acheter du pain")]
        )
    )
    events = await _collect(process_message_stream("rappelle-moi le pain", deps))
    deps.tasks.create.assert_awaited_once()
    assert events[-1]["meta"]["intent"] == "task"
    assert _visible_text(events) == "C'est noté !"


async def test_stream_search_intent_replaces_then_streams_summary(deps: BotDeps) -> None:
    deps.llm.chat_stream = MagicMock(
        return_value=_astream(
            ["Je regarde ça.\n", _meta_block("search", search_query="prix vélo cargo")]
        )
    )
    deps.llm.call_with_search_stream = MagicMock(return_value=_astream(["Voici ", "le résumé."]))
    events = await _collect(process_message_stream("cherche le prix des vélos cargo", deps))
    types = [e["type"] for e in events]
    assert "replace" in types  # reset de l'intro avant le résumé
    assert _visible_text(events) == "Voici le résumé."
    assert "assistant: Voici le résumé." in deps.history
    deps.search.search.assert_awaited_once_with("prix vélo cargo")


async def test_stream_search_frame_sequence_golden(deps: BotDeps) -> None:
    """Golden : séquence complète des frames d'un intent search streamé.

    Verrouille le protocole consommé par la PWA : deltas de l'intro
    optimiste, puis un unique replace("") qui efface l'intro, puis les
    deltas du résumé du second appel LLM, puis un unique done final
    portant la Meta. Robuste au découpage en chunks (les deltas contigus
    sont concaténés) mais strict sur l'ordre et le contenu.
    """
    deps.llm.chat_stream = MagicMock(
        return_value=_astream(
            ["Je vérifie.\n", _meta_block("search", search_query="prix vélo cargo")]
        )
    )
    deps.llm.call_with_search_stream = MagicMock(return_value=_astream(["Voici ", "le résumé."]))
    events = await _collect(process_message_stream("cherche le prix des vélos cargo", deps))

    types = [e["type"] for e in events]
    assert types.count("replace") == 1
    assert types.count("done") == 1
    assert types[-1] == "done"

    replace_idx = types.index("replace")
    intro_deltas = events[:replace_idx]
    summary_deltas = events[replace_idx + 1 : -1]
    assert all(e["type"] == "delta" for e in intro_deltas)
    assert all(e["type"] == "delta" for e in summary_deltas)

    # Le \n final de l'intro est retenu par MetaStreamFilter (il précède le
    # bloc <meta>) : seul le texte utile atteint le client.
    assert "".join(e["text"] for e in intro_deltas) == "Je vérifie."
    assert events[replace_idx]["text"] == ""
    assert "".join(e["text"] for e in summary_deltas) == "Voici le résumé."
    assert events[-1]["meta"]["intent"] == "search"


async def test_stream_weather_intent_replaces_with_handler_text(deps: BotDeps) -> None:
    """Un intent à handler Python (weather) doit émettre un replace avec le texte final."""
    deps.llm.chat_stream = MagicMock(
        return_value=_astream(["Je regarde le ciel.\n", _meta_block("weather")])
    )
    events = await _collect(process_message_stream("quel temps fait-il ?", deps))
    replaces = [e for e in events if e["type"] == "replace"]
    assert len(replaces) == 1
    # get_forecast mocké → [] → message "aucune prévision" du handler.
    assert "Aucune prévision" in replaces[0]["text"]
    assert events[-1]["meta"]["intent"] == "weather"


async def test_stream_invalid_meta_falls_back(deps: BotDeps) -> None:
    """Sans bloc <meta>, on remplace par FALLBACK_TEXT et on n'altère pas l'history."""
    deps.llm.chat_stream = MagicMock(return_value=_astream(["Texte sans aucun bloc meta."]))
    events = await _collect(process_message_stream("salut", deps))
    assert _visible_text(events) == FALLBACK_TEXT
    assert events[-1]["type"] == "done"
    assert events[-1]["meta"]["intent"] == "answer"
    assert len(deps.history) == 0


async def test_stream_passes_system_prompt_and_user_message(deps: BotDeps) -> None:
    await _collect(process_message_stream("salut", deps))
    messages: list[dict[str, Any]] = deps.llm.chat_stream.call_args.kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1] == {"role": "user", "content": "salut"}
