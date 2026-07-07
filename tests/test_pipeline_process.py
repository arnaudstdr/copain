"""Tests d'intégration du pipeline `process_message` avec toutes les dépendances mockées.

Objectif : vérifier l'orchestration LLM → parser → side_effects → scheduler.
C'est le cœur du bot, les autres tests ne couvraient que chaque brique isolée.
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.memory.manager import DepotMatch
from bot.pipeline import BotDeps, process_message
from bot.profile import UserProfile
from tests.conftest import make_settings


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
    fuel_type: str | None = None,
    fuel_radius_km: float | None = None,
    fuel_location: str | None = None,
    weather_location: str | None = None,
    weather_when: str | None = None,
    depot_content: str | None = None,
    depot_kind: str | None = None,
    depot_action: str | None = None,
    depot_thought_id: int | None = None,
    expense_action: str | None = None,
    expense_amount: float | None = None,
    expense_label: str | None = None,
    expense_category: str | None = None,
    expense_recurring_key: str | None = None,
    expense_when: str | None = None,
    expense_shared: bool = False,
    expense_starts_cycle: bool = False,
    search_query: str | None = None,
    memory_query: str | None = None,
    response_text: str = "Réponse texte.",
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
        "fuel": {
            "fuel_type": fuel_type,
            "radius_km": fuel_radius_km,
            "location": fuel_location,
        },
        "weather": {
            "location": weather_location,
            "when": weather_when,
        },
        "depot": {
            "content": depot_content,
            "kind": depot_kind,
            "action": depot_action,
            "thought_id": depot_thought_id,
        },
        "expense": {
            "action": expense_action,
            "amount": expense_amount,
            "label": expense_label,
            "category": expense_category,
            "recurring_key": expense_recurring_key,
            "when": expense_when,
            "shared": expense_shared,
            "starts_cycle": expense_starts_cycle,
        },
        "search_query": search_query,
        "memory_query": memory_query,
    }
    return f"{response_text}\n<meta>{json.dumps(meta)}</meta>"


@pytest.fixture
def deps() -> BotDeps:
    """BotDeps entièrement mocké pour isoler process_message des vraies dépendances."""
    settings = make_settings()

    memory = MagicMock()
    memory.retrieve_context = AsyncMock(return_value=[])
    memory.store = AsyncMock()
    memory.store_depot = AsyncMock()
    memory.find_similar_depots = AsyncMock(return_value=[])

    llm = MagicMock()
    llm.call = AsyncMock(return_value=_meta_block(intent="answer"))
    llm.call_with_search = AsyncMock(return_value="Résumé de la recherche")
    llm.call_with_recall = AsyncMock(return_value="Voici ce que tu avais noté.")
    llm.chat = AsyncMock(return_value="Résumé des articles")

    tasks = MagicMock()
    fake_task = MagicMock()
    fake_task.id = 123
    fake_task.content = "acheter du pain"
    tasks.create = AsyncMock(return_value=fake_task)

    thoughts = MagicMock()
    fake_thought = MagicMock()
    fake_thought.id = 7
    fake_thought.content = "j'ai peur pour les finances de mon fils"
    fake_thought.kind = "worry"
    thoughts.create = AsyncMock(return_value=fake_thought)
    thoughts.list_since = AsyncMock(return_value=[])
    thoughts.list_open = AsyncMock(return_value=[])
    thoughts.close = AsyncMock(return_value=True)

    expenses = MagicMock()
    fake_expense = MagicMock()
    fake_expense.id = 42
    expenses.add_punctual = AsyncMock(return_value=fake_expense)
    expenses.add_income = AsyncMock(return_value=fake_expense)
    expenses.tick_recurring = AsyncMock(return_value=fake_expense)
    expenses.tick_recurring_once = AsyncMock(return_value=fake_expense)
    expenses.start_cycle = AsyncMock(return_value=fake_expense)
    expenses.list_for_month = AsyncMock(return_value=[])
    expenses.list_for_cycle = AsyncMock(return_value=[])
    expenses.is_recurring_ticked_this_month = AsyncMock(return_value=False)
    expenses.is_recurring_ticked_in_cycle = AsyncMock(return_value=False)
    # Aucune ancre déclarée → bornes mois civil (fallback).
    expenses.current_cycle_bounds = AsyncMock(
        side_effect=lambda today: (
            today.replace(day=1),
            (
                today.replace(year=today.year + 1, month=1, day=1)
                if today.month == 12
                else today.replace(month=today.month + 1, day=1)
            ),
        )
    )

    scheduler = MagicMock()
    search = MagicMock()
    search.search = AsyncMock(return_value=[])
    rss = MagicMock()
    rss_fetcher = MagicMock()
    calendar = MagicMock()
    fuel = MagicMock()
    fuel.find_cheapest = AsyncMock(return_value=[])
    overpass = MagicMock()
    overpass.find_fuel_stations = AsyncMock(return_value=[])
    geocoder = MagicMock()
    geocoder.geocode_fr = AsyncMock(return_value=None)
    weather = MagicMock()
    weather.get_forecast = AsyncMock(return_value=[])

    location_events = MagicMock()
    location_events.get_current_location = AsyncMock(return_value=None)

    proactivity = MagicMock()
    proactivity.on_location_event = AsyncMock()

    return BotDeps(
        settings=settings,
        llm=llm,
        memory=memory,
        tasks=tasks,
        thoughts=thoughts,
        expenses=expenses,
        scheduler=scheduler,
        search=search,
        rss=rss,
        rss_fetcher=rss_fetcher,
        calendar=calendar,
        fuel=fuel,
        overpass=overpass,
        geocoder=geocoder,
        weather=weather,
        news=MagicMock(),
        foryou=MagicMock(),
        profile=UserProfile(raw_yaml="", is_loaded=False),
        location_events=location_events,
        proactivity=proactivity,
        history=deque(maxlen=6),
    )


async def test_process_answer_intent_returns_text(deps: BotDeps) -> None:
    text, meta = await process_message("salut", deps=deps)
    assert text == "Réponse texte."
    assert meta["intent"] == "answer"
    deps.memory.store.assert_not_called()
    deps.tasks.create.assert_not_called()
    deps.scheduler.add_reminder.assert_not_called()


async def test_process_fetches_current_location_for_each_call(deps: BotDeps) -> None:
    """Le pipeline doit consulter le LocationEventStore pour injecter la position."""
    await process_message("salut", deps=deps)
    deps.location_events.get_current_location.assert_awaited()


async def test_process_voice_mode_propagates_to_prompt(deps: BotDeps) -> None:
    """voice_mode=True doit produire un system prompt contenant le préambule TTS."""
    await process_message("salut", deps=deps, voice_mode=True)
    system_prompt = deps.llm.call.await_args.kwargs["system"]
    assert "TU RÉPONDS PAR LA VOIX" in system_prompt


async def test_process_default_mode_no_voice_preamble(deps: BotDeps) -> None:
    await process_message("salut", deps=deps)
    system_prompt = deps.llm.call.await_args.kwargs["system"]
    assert "TU RÉPONDS PAR LA VOIX" not in system_prompt


async def test_process_conversation_mode_adds_dialogue_preamble(deps: BotDeps) -> None:
    """conversation_mode=True empile le préambule dialogue ET le préambule vocal."""
    await process_message("salut", deps=deps, voice_mode=True, conversation_mode=True)
    system_prompt = deps.llm.call.await_args.kwargs["system"]
    assert "CONVERSATION VOCALE CONTINUE" in system_prompt
    assert "TU RÉPONDS PAR LA VOIX" in system_prompt


async def test_process_default_mode_no_conversation_preamble(deps: BotDeps) -> None:
    await process_message("salut", deps=deps)
    system_prompt = deps.llm.call.await_args.kwargs["system"]
    assert "CONVERSATION VOCALE CONTINUE" not in system_prompt


async def test_process_stores_memory_when_flagged(deps: BotDeps) -> None:
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="answer", store_memory=True, memory_content="Arnaud habite Sélestat"
        )
    )
    await process_message("j'habite Sélestat", deps=deps)
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
    await process_message("rappelle-moi d'acheter du pain demain 18h", deps=deps)
    deps.tasks.create.assert_awaited_once()
    deps.scheduler.add_reminder.assert_called_once()
    call = deps.scheduler.add_reminder.call_args
    assert call.kwargs["task_id"] == 123
    assert call.kwargs["content"] == "acheter du pain"
    assert call.kwargs["due_at"].hour == 18


async def test_process_task_without_due_skips_reminder(deps: BotDeps) -> None:
    deps.llm.call = AsyncMock(
        return_value=_meta_block(intent="task", task_content="ranger le bureau", task_due=None)
    )
    await process_message("je dois ranger mon bureau", deps=deps)
    deps.tasks.create.assert_awaited_once()
    deps.scheduler.add_reminder.assert_not_called()


async def test_process_depot_intent_persists_thought_and_indexes_chroma(
    deps: BotDeps,
) -> None:
    """Un intent `depot` doit créer une ligne thoughts + indexer ChromaDB avec tag."""
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="depot",
            depot_content="j'ai peur pour les finances de mon fils",
            depot_kind="worry",
            response_text="Noté.",
        )
    )
    text, meta = await process_message("j'ai peur pour les finances de mon fils", deps=deps)

    deps.thoughts.create.assert_awaited_once_with(
        content="j'ai peur pour les finances de mon fils",
        kind="worry",
    )
    deps.memory.store_depot.assert_awaited_once_with(
        content="j'ai peur pour les finances de mon fils",
        thought_id=7,
        thought_kind="worry",
    )
    # La réponse texte du LLM ("Noté.") est préservée telle quelle.
    assert text == "Noté."
    assert meta["intent"] == "depot"


async def test_process_depot_intent_skips_generic_memory_store(deps: BotDeps) -> None:
    """Même si store_memory=true par accident, un dépôt ne déclenche pas memory.store."""
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="depot",
            store_memory=True,
            memory_content="ne doit pas être stocké",
            depot_content="pensée déposée",
            depot_kind="note",
        )
    )
    await process_message("pensée déposée", deps=deps)
    deps.thoughts.create.assert_awaited_once()
    deps.memory.store.assert_not_called()


async def test_process_depot_with_null_content_skips_persistence(deps: BotDeps) -> None:
    """intent=depot mais content=null → on n'écrit rien (LLM mal calibré)."""
    deps.llm.call = AsyncMock(
        return_value=_meta_block(intent="depot", depot_content=None, depot_kind=None)
    )
    await process_message("bla", deps=deps)
    deps.thoughts.create.assert_not_called()
    deps.memory.store_depot.assert_not_called()


async def test_process_depot_chroma_failure_is_swallowed(deps: BotDeps) -> None:
    """Si ChromaDB plante, le dépôt SQLite reste persistant et l'utilisateur reçoit la réponse."""
    deps.memory.store_depot = AsyncMock(side_effect=RuntimeError("chroma down"))
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="depot",
            depot_content="une note libre",
            depot_kind="note",
            response_text="OK.",
        )
    )
    text, _ = await process_message("une note libre", deps=deps)
    deps.thoughts.create.assert_awaited_once()
    assert text == "OK."


# --- détection de boucle au dépôt (suffixe accusé) ---------------------------


def _neighbour_thought(thought_id: int, days_ago: float) -> MagicMock:
    """Voisin SQLite factice : `created_at` naïf UTC (comme aiosqlite le renvoie)."""
    t = MagicMock()
    t.id = thought_id
    t.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_ago)
    return t


def _depot_llm_response(response_text: str = "Noté.") -> AsyncMock:
    return AsyncMock(
        return_value=_meta_block(
            intent="depot",
            depot_content="j'ai peur pour les finances de mon fils",
            depot_kind="worry",
            response_text=response_text,
        )
    )


async def test_process_depot_loop_detected_suffixes_ack(deps: BotDeps) -> None:
    """2 voisins similaires dans la fenêtre → accusé suffixé « 3e fois que ça revient »."""
    deps.llm.call = _depot_llm_response()
    deps.memory.find_similar_depots = AsyncMock(
        return_value=[
            DepotMatch(thought_id=3, content="peur pour les finances", distance=0.1),
            DepotMatch(thought_id=5, content="inquiet pour l'argent", distance=0.2),
        ]
    )
    deps.thoughts.list_since = AsyncMock(
        return_value=[_neighbour_thought(3, days_ago=2), _neighbour_thought(5, days_ago=10)]
    )

    text, _ = await process_message("j'ai peur pour les finances de mon fils", deps=deps)

    assert text == "Noté. — 3e fois que ça revient."
    call = deps.memory.find_similar_depots.await_args
    assert call.kwargs["max_distance"] == 0.25
    assert call.kwargs["top_k"] == 8


async def test_process_depot_loop_of_five_suffixes_5e(deps: BotDeps) -> None:
    """4 voisins valides → « 5e fois que ça revient »."""
    deps.llm.call = _depot_llm_response()
    deps.memory.find_similar_depots = AsyncMock(
        return_value=[
            DepotMatch(thought_id=i, content=f"souci {i}", distance=0.1) for i in (1, 2, 3, 4)
        ]
    )
    deps.thoughts.list_since = AsyncMock(
        return_value=[_neighbour_thought(i, days_ago=i) for i in (1, 2, 3, 4)]
    )

    text, _ = await process_message("encore ce souci", deps=deps)

    assert text == "Noté. — 5e fois que ça revient."


async def test_process_depot_without_neighbours_keeps_ack(deps: BotDeps) -> None:
    """Aucun voisin similaire → accusé inchangé."""
    deps.llm.call = _depot_llm_response()

    text, _ = await process_message("j'ai peur pour les finances de mon fils", deps=deps)

    assert text == "Noté."


async def test_process_depot_self_match_excluded_from_loop(deps: BotDeps) -> None:
    """Le dépôt vient d'être indexé : son auto-match ne compte pas (2 membres < 3)."""
    deps.llm.call = _depot_llm_response()
    deps.memory.find_similar_depots = AsyncMock(
        return_value=[
            DepotMatch(thought_id=7, content="le dépôt lui-même", distance=0.0),
            DepotMatch(thought_id=3, content="un seul voisin", distance=0.1),
        ]
    )
    deps.thoughts.list_since = AsyncMock(return_value=[_neighbour_thought(3, days_ago=2)])

    text, _ = await process_message("j'ai peur pour les finances de mon fils", deps=deps)

    assert text == "Noté."


async def test_process_depot_neighbour_outside_window_excluded(deps: BotDeps) -> None:
    """Un voisin créé hors fenêtre 30 j ne compte pas dans la boucle."""
    deps.llm.call = _depot_llm_response()
    deps.memory.find_similar_depots = AsyncMock(
        return_value=[
            DepotMatch(thought_id=3, content="récent", distance=0.1),
            DepotMatch(thought_id=5, content="trop vieux", distance=0.2),
        ]
    )
    deps.thoughts.list_since = AsyncMock(
        return_value=[_neighbour_thought(3, days_ago=2), _neighbour_thought(5, days_ago=31)]
    )

    text, _ = await process_message("j'ai peur pour les finances de mon fils", deps=deps)

    assert text == "Noté."


async def test_process_depot_orphan_match_ignored(deps: BotDeps) -> None:
    """Un match ChromaDB absent de SQLite (orphelin) est ignoré du comptage."""
    deps.llm.call = _depot_llm_response()
    deps.memory.find_similar_depots = AsyncMock(
        return_value=[
            DepotMatch(thought_id=3, content="voisin valide", distance=0.1),
            DepotMatch(thought_id=999, content="orphelin chroma", distance=0.2),
        ]
    )
    deps.thoughts.list_since = AsyncMock(return_value=[_neighbour_thought(3, days_ago=2)])

    text, _ = await process_message("j'ai peur pour les finances de mon fils", deps=deps)

    assert text == "Noté."


async def test_process_depot_loop_detection_failure_keeps_ack(deps: BotDeps) -> None:
    """ChromaDB en échec sur la similarité → accusé intact, pas d'exception (fail-soft)."""
    deps.llm.call = _depot_llm_response()
    deps.memory.find_similar_depots = AsyncMock(side_effect=RuntimeError("chroma down"))

    text, _ = await process_message("j'ai peur pour les finances de mon fils", deps=deps)

    assert text == "Noté."


async def test_process_depot_indexing_failure_skips_loop_detection(deps: BotDeps) -> None:
    """Si store_depot échoue, le dépôt n'est pas dans ChromaDB → pas de recherche de boucle."""
    deps.llm.call = _depot_llm_response()
    deps.memory.store_depot = AsyncMock(side_effect=RuntimeError("chroma down"))

    text, _ = await process_message("j'ai peur pour les finances de mon fils", deps=deps)

    deps.memory.find_similar_depots.assert_not_called()
    assert text == "Noté."


async def test_process_depot_history_records_suffixed_text(deps: BotDeps) -> None:
    """L'history roulante porte le texte final AVEC suffixe (cohérence des tours suivants)."""
    deps.llm.call = _depot_llm_response()
    deps.memory.find_similar_depots = AsyncMock(
        return_value=[
            DepotMatch(thought_id=3, content="a", distance=0.1),
            DepotMatch(thought_id=5, content="b", distance=0.2),
        ]
    )
    deps.thoughts.list_since = AsyncMock(
        return_value=[_neighbour_thought(3, days_ago=2), _neighbour_thought(5, days_ago=10)]
    )

    await process_message("j'ai peur pour les finances de mon fils", deps=deps)

    assert "assistant: Noté. — 3e fois que ça revient." in deps.history


def _open_worry(thought_id: int, content: str = "peur pour le contrôle technique") -> MagicMock:
    """Souci ouvert factice retourné par `list_open` (created_at naïf UTC)."""
    t = MagicMock()
    t.id = thought_id
    t.content = content
    t.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=5)
    return t


def _depot_close_llm_response(
    thought_id: int | None, response_text: str = "Bien, je le range."
) -> AsyncMock:
    return AsyncMock(
        return_value=_meta_block(
            intent="depot",
            depot_action="close",
            depot_thought_id=thought_id,
            response_text=response_text,
        )
    )


async def test_process_depot_close_valid_id_closes_and_keeps_ack(deps: BotDeps) -> None:
    """action=close avec un souci réellement ouvert → close() appelé, accusé LLM conservé."""
    deps.thoughts.list_open = AsyncMock(return_value=[_open_worry(12)])
    deps.llm.call = _depot_close_llm_response(thought_id=12)

    text, meta = await process_message("c'est bon pour le contrôle technique", deps=deps)

    assert text == "Bien, je le range."
    assert meta["intent"] == "depot"
    deps.thoughts.close.assert_awaited_once_with(12)
    # Une clôture n'est pas un dépôt : ni persistance, ni détection de boucle.
    deps.thoughts.create.assert_not_called()
    deps.memory.store_depot.assert_not_called()
    deps.memory.find_similar_depots.assert_not_called()


async def test_process_depot_close_unknown_id_replaces_with_honest_text(deps: BotDeps) -> None:
    """thought_id halluciné (absent des soucis ouverts) → aucun side effect, réponse honnête."""
    deps.thoughts.list_open = AsyncMock(return_value=[_open_worry(12)])
    deps.llm.call = _depot_close_llm_response(thought_id=999)

    text, _ = await process_message("c'est réglé", deps=deps)

    assert "pas retrouvé" in text
    deps.thoughts.close.assert_not_called()
    deps.memory.store_depot.assert_not_called()


async def test_process_depot_close_already_closed_id_treated_as_invalid(deps: BotDeps) -> None:
    """Un id existant mais déjà clos n'est pas dans list_open → même chemin que l'hallucination."""
    deps.thoughts.list_open = AsyncMock(return_value=[])
    deps.llm.call = _depot_close_llm_response(thought_id=12)

    text, _ = await process_message("c'est réglé pour le contrôle technique", deps=deps)

    assert "pas retrouvé" in text
    deps.thoughts.close.assert_not_called()


async def test_process_depot_close_missing_thought_id_replaces(deps: BotDeps) -> None:
    """action=close sans thought_id → réponse honnête, aucun side effect."""
    deps.thoughts.list_open = AsyncMock(return_value=[_open_worry(12)])
    deps.llm.call = _depot_close_llm_response(thought_id=None)

    text, _ = await process_message("c'est réglé", deps=deps)

    assert "pas retrouvé" in text
    deps.thoughts.close.assert_not_called()


async def test_process_depot_close_records_final_text_in_history(deps: BotDeps) -> None:
    """L'history porte le texte réellement renvoyé (remplacement honnête inclus)."""
    deps.thoughts.list_open = AsyncMock(return_value=[])
    deps.llm.call = _depot_close_llm_response(thought_id=999)

    text, _ = await process_message("c'est réglé", deps=deps)

    assert "pas retrouvé" in text
    assert f"assistant: {text}" in deps.history


async def test_process_injects_open_worries_into_prompt(deps: BotDeps) -> None:
    """Les soucis ouverts (kind=worry, max 10) sont injectés dans le system prompt."""
    deps.thoughts.list_open = AsyncMock(return_value=[_open_worry(12)])

    await process_message("salut", deps=deps)

    deps.thoughts.list_open.assert_awaited_once_with(kinds=["worry"], limit=10)
    system_prompt = deps.llm.call.await_args.kwargs["system"]
    assert "--- Soucis ouverts" in system_prompt
    assert "[id 12]" in system_prompt
    assert "peur pour le contrôle technique" in system_prompt


async def test_process_open_worries_failure_is_fail_soft(deps: BotDeps) -> None:
    """SQLite en échec sur list_open → prompt sans la section, réponse normale."""
    deps.thoughts.list_open = AsyncMock(side_effect=RuntimeError("sqlite down"))

    text, _ = await process_message("salut", deps=deps)

    assert text == "Réponse texte."
    system_prompt = deps.llm.call.await_args.kwargs["system"]
    assert "--- Soucis ouverts" not in system_prompt


async def test_process_search_intent_relaunches_llm_with_results(
    deps: BotDeps,
) -> None:
    deps.llm.call = AsyncMock(
        return_value=_meta_block(intent="search", search_query="météo Paris demain")
    )
    deps.search.search = AsyncMock(return_value=[{"title": "T", "url": "u", "snippet": "s"}])
    text, _ = await process_message("il fera quel temps demain ?", deps=deps)
    deps.search.search.assert_awaited_once_with("météo Paris demain")
    deps.llm.call_with_search.assert_awaited_once()
    assert text == "Résumé de la recherche"


async def test_process_memory_intent_recalls_and_reformulates(deps: BotDeps) -> None:
    """intent=memory → retrieve_context sur la query → reformulation LLM."""
    deps.llm.call = AsyncMock(return_value=_meta_block(intent="memory", memory_query="le garage"))
    deps.memory.retrieve_context = AsyncMock(return_value=["note sur le garage"])
    text, _ = await process_message("j'avais noté quoi sur le garage ?", deps=deps)
    # retrieve_context sert aussi au RAG de _build_prompt (top_k=5) ; on vérifie
    # l'appel dédié au recall (top_k=8) parmi les appels.
    deps.memory.retrieve_context.assert_any_await("le garage", top_k=8)
    deps.llm.call_with_recall.assert_awaited_once()
    assert text == "Voici ce que tu avais noté."


async def test_process_memory_intent_empty_recall_returns_fixed_text(deps: BotDeps) -> None:
    """Recall sans résultat → texte fixe, pas de second appel LLM."""
    deps.llm.call = AsyncMock(return_value=_meta_block(intent="memory", memory_query="licorne"))
    deps.memory.retrieve_context = AsyncMock(return_value=[])
    text, _ = await process_message("j'avais noté quoi sur les licornes ?", deps=deps)
    deps.llm.call_with_recall.assert_not_awaited()
    assert text == "Je n'ai rien noté là-dessus."


async def test_safe_recall_returns_empty_on_retrieval_failure(deps: BotDeps) -> None:
    """_safe_recall isole une panne d'embed (fail-soft) → liste vide."""
    from bot.memory.embeddings import EmbeddingError
    from bot.pipeline.core import _safe_recall

    deps.memory.retrieve_context = AsyncMock(side_effect=EmbeddingError("ollama down"))
    assert await _safe_recall(deps, "le garage") == []


async def test_process_feed_list_returns_formatted_list(deps: BotDeps) -> None:
    deps.llm.call = AsyncMock(return_value=_meta_block(intent="feed", feed_action="list"))
    deps.rss.list = AsyncMock(return_value=[])
    text, _ = await process_message("mes flux ?", deps=deps)
    assert "Aucun flux enregistré" in text


async def test_process_event_create_calls_calendar(deps: BotDeps) -> None:
    fake_event = MagicMock()
    fake_event.title = "RDV dentiste"
    fake_event.calendar_name = "Personnel"
    from datetime import UTC, datetime

    fake_event.start = datetime(2026, 4, 22, 15, 0, tzinfo=UTC)
    deps.calendar.is_connected = True
    deps.calendar.create_event = AsyncMock(return_value=fake_event)
    deps.calendar.list_all_between = AsyncMock(return_value=[])
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="event",
            event_action="create",
            event_title="RDV dentiste",
            event_start="mardi 15h",
        )
    )
    text, _ = await process_message("mets un RDV dentiste mardi 15h", deps=deps)
    deps.calendar.create_event.assert_awaited_once()
    assert "RDV dentiste" in text
    assert "Chevauche" not in text


async def test_process_event_create_flags_overlap(deps: BotDeps) -> None:
    """Si list_all_between renvoie un évent existant, la confirmation contient un warning."""
    from datetime import UTC, datetime

    fake_event = MagicMock()
    fake_event.title = "RDV dentiste"
    fake_event.calendar_name = "Personnel"
    fake_event.start = datetime(2026, 4, 22, 15, 0, tzinfo=UTC)

    existing = MagicMock()
    existing.title = "Réunion équipe"
    existing.start = datetime(2026, 4, 22, 14, 30, tzinfo=UTC)
    existing.end = datetime(2026, 4, 22, 15, 30, tzinfo=UTC)

    deps.calendar.is_connected = True
    deps.calendar.list_all_between = AsyncMock(return_value=[existing])
    deps.calendar.create_event = AsyncMock(return_value=fake_event)
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="event",
            event_action="create",
            event_title="RDV dentiste",
            event_start="mardi 15h",
        )
    )
    text, _ = await process_message("mets un RDV dentiste mardi 15h", deps=deps)
    # L'évent est quand même créé (warn-only).
    deps.calendar.create_event.assert_awaited_once()
    # Et la réponse contient le warning.
    assert "Chevauche" in text
    assert "Réunion équipe" in text


async def test_process_event_create_continues_if_overlap_check_fails(
    deps: BotDeps,
) -> None:
    """Une erreur sur list_all_between ne doit pas bloquer la création."""
    from datetime import UTC, datetime

    from bot.calendar.client import ICloudCalendarError

    fake_event = MagicMock()
    fake_event.title = "RDV dentiste"
    fake_event.calendar_name = "Personnel"
    fake_event.start = datetime(2026, 4, 22, 15, 0, tzinfo=UTC)

    deps.calendar.is_connected = True
    deps.calendar.list_all_between = AsyncMock(side_effect=ICloudCalendarError("flaky"))
    deps.calendar.create_event = AsyncMock(return_value=fake_event)
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="event",
            event_action="create",
            event_title="RDV dentiste",
            event_start="mardi 15h",
        )
    )
    text, _ = await process_message("mets un RDV dentiste mardi 15h", deps=deps)
    deps.calendar.create_event.assert_awaited_once()
    assert "RDV dentiste" in text


async def test_process_meta_parse_failure_returns_fallback(deps: BotDeps) -> None:
    deps.llm.call = AsyncMock(return_value="Pas de bloc meta ici.")
    text, meta = await process_message("blabla", deps=deps)
    from bot.pipeline import FALLBACK_TEXT

    assert text == FALLBACK_TEXT
    # Le meta de fallback est "answer" pour ne déclencher aucun side effect
    # côté pipeline ni refresh côté API.
    assert meta["intent"] == "answer"


async def test_process_updates_history_with_user_and_assistant(deps: BotDeps) -> None:
    await process_message("salut", deps=deps)
    await process_message("ça va", deps=deps)
    assert len(deps.history) == 4
    assert deps.history[0].startswith("user: salut")
    assert deps.history[1].startswith("assistant:")
    assert deps.history[2].startswith("user: ça va")


async def test_process_history_is_bounded_by_maxlen(deps: BotDeps) -> None:
    """10 échanges → 20 entrées → deque tronqué à 6 (MAX_HISTORY)."""
    for i in range(10):
        await process_message(f"msg {i}", deps=deps)
    assert len(deps.history) == 6
    # Les dernières entrées sont conservées, les plus anciennes purgées.
    assert deps.history[-2].startswith("user: msg 9")


async def test_process_with_image_prepends_photo_tag_in_history(
    deps: BotDeps,
) -> None:
    await process_message("décris", deps=deps, images=[b"fakepng"])
    assert deps.history[0].startswith("user: [photo] décris")


async def test_process_empty_text_with_image_uses_default_prompt(
    deps: BotDeps,
) -> None:
    await process_message("", deps=deps, images=[b"fakepng"])
    # Vérifie que le LLM a bien reçu le prompt par défaut (pas une string vide)
    call_kwargs = deps.llm.call.call_args.kwargs
    assert "Analyse cette image" in call_kwargs["user"]


async def test_process_fuel_intent_home_falls_back_to_home_coords(
    deps: BotDeps,
) -> None:
    """Sans location → on interroge l'API avec HOME_LAT/HOME_LON."""
    from datetime import UTC, datetime

    from bot.fuel.models import FuelStation

    station = FuelStation(
        id="A",
        address="12 rue Y",
        city="Sélestat",
        postal_code="67600",
        lat=48.26,
        lon=7.45,
        distance_km=2.3,
        fuel_type="gazole",
        price_eur=1.689,
        updated_at=datetime.now(UTC),
    )
    deps.fuel.find_cheapest = AsyncMock(return_value=[station])
    deps.llm.call = AsyncMock(return_value=_meta_block(intent="fuel", fuel_type="gazole"))

    text, _ = await process_message("gazole pas cher ?", deps=deps)

    deps.geocoder.geocode_fr.assert_not_called()
    deps.fuel.find_cheapest.assert_awaited_once()
    call_kwargs = deps.fuel.find_cheapest.await_args.kwargs
    assert call_kwargs["fuel_type"] == "gazole"
    assert call_kwargs["center"].lat == 48.26
    assert call_kwargs["center"].lon == 7.45
    assert call_kwargs["radius_km"] == 10.0
    assert "Sélestat" in text
    assert "1.689 €" in text


async def test_process_fuel_intent_with_location_calls_geocoder(
    deps: BotDeps,
) -> None:
    """Avec location → on appelle Nominatim et on utilise les coords retournées."""
    from bot.fuel.models import GeoPoint

    deps.geocoder.geocode_fr = AsyncMock(return_value=GeoPoint(lat=48.08, lon=7.36))
    deps.fuel.find_cheapest = AsyncMock(return_value=[])
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="fuel",
            fuel_type="sp98",
            fuel_radius_km=5.0,
            fuel_location="Colmar",
        )
    )

    text, _ = await process_message("SP98 à Colmar dans 5 km", deps=deps)

    deps.geocoder.geocode_fr.assert_awaited_once_with("Colmar")
    call_kwargs = deps.fuel.find_cheapest.await_args.kwargs
    assert call_kwargs["center"].lat == pytest.approx(48.08)
    assert call_kwargs["radius_km"] == 5.0
    assert "Colmar" in text


async def test_process_fuel_unknown_fuel_type_returns_hint(
    deps: BotDeps,
) -> None:
    deps.llm.call = AsyncMock(return_value=_meta_block(intent="fuel", fuel_type="charbon"))
    text, _ = await process_message("charbon pas cher", deps=deps)
    assert "Je ne reconnais pas" in text
    deps.fuel.find_cheapest.assert_not_called()


async def test_process_fuel_diesel_synonym_is_normalized(deps: BotDeps) -> None:
    """Le LLM peut envoyer 'diesel' — on doit mapper vers 'gazole'."""
    deps.fuel.find_cheapest = AsyncMock(return_value=[])
    deps.llm.call = AsyncMock(return_value=_meta_block(intent="fuel", fuel_type="diesel"))

    await process_message("diesel pas cher", deps=deps)

    call_kwargs = deps.fuel.find_cheapest.await_args.kwargs
    assert call_kwargs["fuel_type"] == "gazole"


async def test_process_fuel_location_not_found_returns_message(
    deps: BotDeps,
) -> None:
    deps.geocoder.geocode_fr = AsyncMock(return_value=None)
    deps.llm.call = AsyncMock(
        return_value=_meta_block(intent="fuel", fuel_type="gazole", fuel_location="Atlantide")
    )
    text, _ = await process_message("gazole à Atlantide", deps=deps)
    assert "Atlantide" in text
    deps.fuel.find_cheapest.assert_not_called()


async def test_process_weather_intent_home_default(deps: BotDeps) -> None:
    """Sans location → HOME_LAT/HOME_LON ; sans when → aujourd'hui (1 jour)."""
    from datetime import date

    from bot.weather.client import DailyWeather

    day = DailyWeather(
        city="Sélestat",
        date=date(2026, 6, 3),
        temp_min=10.0,
        temp_max=20.0,
        precipitation_mm=0.0,
        wind_kmh_max=12.0,
        description="ciel dégagé",
        temp_current=15.0,
    )
    deps.weather.get_forecast = AsyncMock(return_value=[day])
    deps.llm.call = AsyncMock(return_value=_meta_block(intent="weather"))

    text, _ = await process_message("quel temps fait-il ?", deps=deps)

    deps.geocoder.geocode_fr.assert_not_called()
    deps.weather.get_forecast.assert_awaited_once()
    call_kwargs = deps.weather.get_forecast.await_args.kwargs
    assert call_kwargs["lat"] == 48.26
    assert call_kwargs["lon"] == 7.45
    assert call_kwargs["city"] == "Sélestat"
    assert call_kwargs["days"] == 1
    assert "Sélestat" in text
    assert "aujourd'hui" in text
    assert "15°C" in text
    assert "ciel dégagé".capitalize() in text


async def test_process_weather_intent_with_location_calls_geocoder(
    deps: BotDeps,
) -> None:
    """Avec location → appel Nominatim + coordonnées utilisées."""
    from datetime import date

    from bot.fuel.models import GeoPoint
    from bot.weather.client import DailyWeather

    deps.geocoder.geocode_fr = AsyncMock(return_value=GeoPoint(lat=48.58, lon=7.75))
    deps.weather.get_forecast = AsyncMock(
        return_value=[
            DailyWeather(
                city="Strasbourg",
                date=date(2026, 6, 3),
                temp_min=8.0,
                temp_max=18.0,
                precipitation_mm=2.0,
                wind_kmh_max=20.0,
                description="pluie faible",
                temp_current=12.0,
            )
        ]
    )
    deps.llm.call = AsyncMock(
        return_value=_meta_block(intent="weather", weather_location="Strasbourg")
    )

    text, _ = await process_message("météo à Strasbourg ?", deps=deps)

    deps.geocoder.geocode_fr.assert_awaited_once_with("Strasbourg")
    call_kwargs = deps.weather.get_forecast.await_args.kwargs
    assert call_kwargs["lat"] == pytest.approx(48.58)
    assert call_kwargs["lon"] == pytest.approx(7.75)
    assert "Strasbourg" in text


async def test_process_weather_demain_requests_two_days_returns_single(
    deps: BotDeps,
) -> None:
    """'demain' → days=2 (jour 0 + jour 1), on retourne juste le jour 1."""
    from datetime import date, timedelta

    from bot.weather.client import DailyWeather

    today = date(2026, 6, 3)
    tomorrow = today + timedelta(days=1)
    forecast = [
        DailyWeather(
            city="Sélestat",
            date=today,
            temp_min=10.0,
            temp_max=20.0,
            precipitation_mm=0.0,
            wind_kmh_max=12.0,
            description="ciel dégagé",
            temp_current=15.0,
        ),
        DailyWeather(
            city="Sélestat",
            date=tomorrow,
            temp_min=12.0,
            temp_max=22.0,
            precipitation_mm=5.0,
            wind_kmh_max=18.0,
            description="averses faibles",
            temp_current=None,
        ),
    ]
    deps.weather.get_forecast = AsyncMock(return_value=forecast)
    deps.llm.call = AsyncMock(return_value=_meta_block(intent="weather", weather_when="demain"))

    text, _ = await process_message("quel temps demain ?", deps=deps)

    call_kwargs = deps.weather.get_forecast.await_args.kwargs
    assert call_kwargs["days"] == 2
    assert "demain" in text
    # Pas de "maintenant" pour un jour futur (temp_current=None).
    assert "maintenant" not in text
    assert "Averses faibles" in text


async def test_process_weather_location_not_found(deps: BotDeps) -> None:
    deps.geocoder.geocode_fr = AsyncMock(return_value=None)
    deps.llm.call = AsyncMock(
        return_value=_meta_block(intent="weather", weather_location="Atlantide")
    )
    text, _ = await process_message("météo à Atlantide", deps=deps)
    assert "Atlantide" in text
    deps.weather.get_forecast.assert_not_called()


async def test_process_expense_spend_calls_add_punctual(deps: BotDeps) -> None:
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="expense",
            expense_action="spend",
            expense_amount=27,
            expense_label="pharmacie",
            expense_category="santé",
            response_text="Noté.",
        )
    )
    text, meta = await process_message("j'ai dépensé 27€ à la pharmacie", deps=deps)
    assert text == "Noté."
    assert meta["intent"] == "expense"
    deps.expenses.add_punctual.assert_awaited_once()
    kwargs = deps.expenses.add_punctual.await_args.kwargs
    assert kwargs["amount_cents"] == 2700
    assert kwargs["label"] == "pharmacie"
    assert kwargs["category"] == "santé"
    assert kwargs["shared"] is False
    # Pas de store_memory générique sur une saisie expense.
    deps.memory.store.assert_not_called()


async def test_process_expense_spend_propagates_shared_true(deps: BotDeps) -> None:
    """Une dépense compte joint doit propager shared=True à add_punctual."""
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="expense",
            expense_action="spend",
            expense_amount=30,
            expense_label="Lidl",
            expense_category="nourriture",
            expense_shared=True,
            response_text="Noté.",
        )
    )
    await process_message("on vient de dépenser 30€ chez Lidl sur le compte joint", deps=deps)
    deps.expenses.add_punctual.assert_awaited_once()
    kwargs = deps.expenses.add_punctual.await_args.kwargs
    assert kwargs["shared"] is True
    assert kwargs["category"] == "nourriture"


async def test_process_image_expense_defers_write(deps: BotDeps) -> None:
    """Une dépense lue depuis une IMAGE (capture Revolut) n'écrit RIEN.

    Le chemin image diffère l'écriture : l'API renverra un brouillon que
    l'utilisateur confirmera via POST /expenses. Aucun appel à add_punctual.
    """
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="expense",
            expense_action="spend",
            expense_amount=23.4,
            expense_label="Lidl",
            expense_category="courses",
            expense_when="hier",
            response_text="Noté.",
        )
    )
    text, meta = await process_message("", deps=deps, images=[b"fakepng"])
    assert meta["intent"] == "expense"
    assert "vérifie" in text.lower()
    deps.expenses.add_punctual.assert_not_awaited()
    # Pas d'entrée dans l'history non plus (aucun effet acté).
    assert len(deps.history) == 0


async def test_process_text_expense_still_writes(deps: BotDeps) -> None:
    """Non-régression : sans image, le chemin texte écrit toujours immédiatement."""
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="expense",
            expense_action="spend",
            expense_amount=23.4,
            expense_label="Lidl",
            expense_category="courses",
            response_text="Noté.",
        )
    )
    await process_message("j'ai dépensé 23,40€ chez Lidl", deps=deps)
    deps.expenses.add_punctual.assert_awaited_once()


async def test_process_expense_income_calls_add_income(deps: BotDeps) -> None:
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="expense",
            expense_action="income",
            expense_amount=2500,
            expense_label="salaire mai",
            response_text="✓ Saisi.",
        )
    )
    await process_message("salaire 2500€", deps=deps)
    deps.expenses.add_income.assert_awaited_once()
    kwargs = deps.expenses.add_income.await_args.kwargs
    assert kwargs["amount_cents"] == 250000
    assert kwargs["label"] == "salaire mai"
    # Sans starts_cycle, aucun cycle n'est démarré.
    deps.expenses.start_cycle.assert_not_awaited()


async def test_process_expense_salary_starts_cycle_and_records_income(deps: BotDeps) -> None:
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="expense",
            expense_action="income",
            expense_amount=2500,
            expense_label="salaire",
            expense_starts_cycle=True,
            response_text="✓ Saisi.",
        )
    )
    await process_message("salaire reçu 2500€", deps=deps)
    deps.expenses.start_cycle.assert_awaited_once()
    deps.expenses.add_income.assert_awaited_once()


async def test_process_expense_salary_without_amount_starts_cycle_only(deps: BotDeps) -> None:
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="expense",
            expense_action="income",
            expense_amount=None,
            expense_label="salaire",
            expense_starts_cycle=True,
            response_text="✓ Cycle démarré.",
        )
    )
    await process_message("j'ai reçu mon salaire", deps=deps)
    # Le cycle démarre même sans montant, mais aucun revenu n'est enregistré.
    deps.expenses.start_cycle.assert_awaited_once()
    deps.expenses.add_income.assert_not_awaited()


async def test_process_expense_tick_recurring_unknown_key_is_noop(
    deps: BotDeps,
) -> None:
    # Profil sans section finances → cfg.find("loyer") = None → skip.
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="expense",
            expense_action="tick_recurring",
            expense_amount=800,
            expense_label="Loyer",
            expense_recurring_key="loyer",
        )
    )
    await process_message("le loyer est passé", deps=deps)
    deps.expenses.tick_recurring_once.assert_not_awaited()


async def test_process_expense_tick_recurring_calls_manager(deps: BotDeps) -> None:
    profile = UserProfile(
        raw_yaml="",
        is_loaded=True,
        data={
            "finances": {
                "recurring": [
                    {
                        "key": "loyer",
                        "label": "Loyer appartement",
                        "amount": 800,
                        "day": 5,
                        "kind": "expense",
                    }
                ]
            }
        },
    )
    deps.profile = profile
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="expense",
            expense_action="tick_recurring",
            expense_amount=800,
            expense_label="Loyer appartement",
            expense_recurring_key="loyer",
        )
    )
    await process_message("le loyer est passé", deps=deps)
    deps.expenses.tick_recurring_once.assert_awaited_once()
    kwargs = deps.expenses.tick_recurring_once.await_args.kwargs
    assert kwargs["recurring_key"] == "loyer"
    assert kwargs["kind"] == "expense"
    assert kwargs["amount_cents"] == 80000  # depuis le YAML (source de vérité)


async def test_process_expense_tick_already_ticked_skipped(deps: BotDeps) -> None:
    profile = UserProfile(
        raw_yaml="",
        is_loaded=True,
        data={
            "finances": {
                "recurring": [
                    {
                        "key": "loyer",
                        "label": "Loyer",
                        "amount": 800,
                        "day": 5,
                        "kind": "expense",
                    }
                ]
            }
        },
    )
    deps.profile = profile
    # Déjà pointée dans le cycle : la variante atomique retourne None.
    deps.expenses.tick_recurring_once = AsyncMock(return_value=None)
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="expense",
            expense_action="tick_recurring",
            expense_amount=800,
            expense_label="Loyer",
            expense_recurring_key="loyer",
        )
    )
    await process_message("le loyer est passé", deps=deps)
    # Le check + insert vivent dans tick_recurring_once : elle est bien
    # appelée, mais aucune écriture directe via tick_recurring.
    deps.expenses.tick_recurring_once.assert_awaited_once()
    deps.expenses.tick_recurring.assert_not_awaited()


async def test_process_expense_negative_amount_silently_skipped(deps: BotDeps) -> None:
    # Le parser rejette les amount < 0, donc le LLM renvoyant un montant
    # négatif déclenche un parse error → fallback meta (intent=answer).
    # On vérifie ici qu'un montant null/zéro n'aboutit pas à un add_*.
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="expense",
            expense_action="spend",
            expense_amount=0,
            expense_label="vide",
        )
    )
    await process_message("dépense 0€", deps=deps)
    deps.expenses.add_punctual.assert_not_awaited()


def _profile_with_pel() -> UserProfile:
    return UserProfile(
        raw_yaml="",
        is_loaded=True,
        data={
            "finances": {
                "recurring": [
                    {
                        "key": "pel",
                        "label": "Versement PEL",
                        "amount": 15,
                        "day": 5,
                        "kind": "saving",
                    }
                ]
            }
        },
    )


async def test_process_expense_tick_recurring_amount_null_uses_yaml_default(
    deps: BotDeps,
) -> None:
    """Sans amount, on retombe sur le montant indicatif du YAML."""
    deps.profile = _profile_with_pel()
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="expense",
            expense_action="tick_recurring",
            expense_amount=None,
            expense_label="Versement PEL",
            expense_recurring_key="pel",
        )
    )
    await process_message("j'ai versé le PEL", deps=deps)
    deps.expenses.tick_recurring_once.assert_awaited_once()
    kwargs = deps.expenses.tick_recurring_once.await_args.kwargs
    assert kwargs["amount_cents"] == 1500  # 15€ du YAML


async def test_process_expense_tick_recurring_amount_override_replaces_yaml(
    deps: BotDeps,
) -> None:
    """Avec un amount explicite, on l'utilise au lieu du YAML (placement variable)."""
    deps.profile = _profile_with_pel()
    deps.llm.call = AsyncMock(
        return_value=_meta_block(
            intent="expense",
            expense_action="tick_recurring",
            expense_amount=11,
            expense_label="Versement PEL",
            expense_recurring_key="pel",
        )
    )
    await process_message("j'ai versé 11€ sur le PEL", deps=deps)
    deps.expenses.tick_recurring_once.assert_awaited_once()
    kwargs = deps.expenses.tick_recurring_once.await_args.kwargs
    assert kwargs["amount_cents"] == 1100  # override LLM
