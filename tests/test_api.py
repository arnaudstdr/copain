"""Tests de la couche HTTP FastAPI (`bot.api`).

Couvre :
- l'authentification via header `X-API-Key` (200 valide, 403 sinon)
- `POST /ask` : pipeline délégué, réponse JSON
- `POST /ask/image` : décodage base64, transmission au pipeline
- `GET /notifications` : lecture + marquage lu
"""

from __future__ import annotations

import base64
from collections import deque
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from bot.api import AppState, create_app
from bot.chat.manager import ChatHistoryManager
from bot.db import create_shared_engine
from bot.locations.store import LocationEventStore
from bot.notifications.store import NotificationStore
from bot.pipeline import BotDeps
from bot.profile import UserProfile
from tests.conftest import make_settings

API_KEY = "test-secret"

# Meta minimal valide utilisé par les tests qui patchent process_message
# pour vérifier les arguments passés (voice_mode, etc.) sans réellement
# faire tourner le pipeline. Intent=answer + tous les sous-objets vides
# pour ne déclencher aucun refresh côté API.
_NEUTRAL_META: dict[str, object] = {
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
    },
    "search_query": None,
}


def _build_deps() -> BotDeps:
    settings = make_settings(api_key=API_KEY)

    memory = MagicMock()
    memory.retrieve_context = AsyncMock(return_value=[])
    memory.store = AsyncMock()

    llm = MagicMock()
    llm.call = AsyncMock(
        return_value=(
            "Bonjour Arnaud.\n"
            '<meta>{"intent":"answer","store_memory":false,"memory_content":null,'
            '"task":{"content":null,"due_str":null},'
            '"feed":{"action":null,"name":null,"url":null},'
            '"event":{"action":null,"title":null,"start_str":null,"end_str":null,'
            '"location":null,"description":null,"range_str":null,"calendar_name":null},'
            '"fuel":{"fuel_type":null,"radius_km":null,"location":null},'
            '"weather":{"location":null,"when":null},"search_query":null}</meta>'
        )
    )

    location_events = MagicMock()
    location_events.get_current_location = AsyncMock(return_value=None)

    proactivity = MagicMock()
    proactivity.on_location_event = AsyncMock()

    expenses = MagicMock()
    expenses.list_for_month = AsyncMock(return_value=[])
    expenses.list_for_cycle = AsyncMock(return_value=[])
    expenses.list_savings_for_year = AsyncMock(return_value=[])
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

    return BotDeps(
        settings=settings,
        llm=llm,
        memory=memory,
        tasks=MagicMock(),
        thoughts=MagicMock(),
        expenses=expenses,
        scheduler=MagicMock(),
        search=MagicMock(),
        rss=MagicMock(),
        rss_fetcher=MagicMock(),
        calendar=MagicMock(),
        fuel=MagicMock(),
        geocoder=MagicMock(),
        weather=MagicMock(),
        news=MagicMock(),
        foryou=MagicMock(),
        profile=UserProfile(raw_yaml="", is_loaded=False),
        location_events=location_events,
        proactivity=proactivity,
        history=deque(maxlen=6),
    )


@pytest.fixture
async def engine(tmp_data_dir: Path) -> AsyncIterator[AsyncEngine]:
    eng = create_shared_engine(tmp_data_dir / "tasks.db")
    yield eng
    await eng.dispose()


@pytest.fixture
async def state(engine: AsyncEngine) -> AppState:
    notifications = NotificationStore(engine)
    await notifications.init_schema()
    location_events = LocationEventStore(engine)
    await location_events.init_schema()
    deps = _build_deps()
    # Remplace le mock par défaut par un vrai store lié à la DB de test,
    # nécessaire pour les tests de l'endpoint /event/location qui doivent
    # réellement persister. Les autres tests qui ne touchent pas à la
    # localisation continuent de fonctionner (table vide → get_current
    # renvoie None, identique au mock).
    deps.location_events = location_events
    return AppState(settings=deps.settings, deps=deps, notifications=notifications)


@pytest.fixture
async def client(state: AppState) -> AsyncIterator[AsyncClient]:
    app = create_app(state)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# --- auth -------------------------------------------------------------------


async def test_ask_without_api_key_returns_403(client: AsyncClient) -> None:
    response = await client.post("/ask", json={"message": "salut"})
    assert response.status_code == 403


async def test_ask_with_wrong_api_key_returns_403(client: AsyncClient) -> None:
    response = await client.post(
        "/ask",
        headers={"X-API-Key": "wrong"},
        json={"message": "salut"},
    )
    assert response.status_code == 403


async def test_notifications_without_api_key_returns_403(client: AsyncClient) -> None:
    response = await client.get("/notifications")
    assert response.status_code == 403


async def test_ask_empty_message_returns_422(client: AsyncClient) -> None:
    response = await client.post(
        "/ask",
        headers={"X-API-Key": API_KEY},
        json={"message": ""},
    )
    assert response.status_code == 422


async def test_ask_oversized_message_returns_422(client: AsyncClient) -> None:
    response = await client.post(
        "/ask",
        headers={"X-API-Key": API_KEY},
        json={"message": "a" * 10_001},
    )
    assert response.status_code == 422


# --- /ask -------------------------------------------------------------------


async def test_ask_returns_response_text(client: AsyncClient) -> None:
    response = await client.post(
        "/ask",
        headers={"X-API-Key": API_KEY},
        json={"message": "salut"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "Bonjour Arnaud."
    assert body["intent"] == "answer"
    assert body["refresh_cards"] == []


async def test_ask_task_intent_lists_cards_to_refresh(client: AsyncClient, state: AppState) -> None:
    """Une action `task` doit indiquer au front que les cards tâches et notifs ont bougé."""
    fake_task = MagicMock()
    fake_task.id = 7
    fake_task.content = "acheter du pain"
    state.deps.tasks.create = AsyncMock(return_value=fake_task)
    state.deps.llm.call = AsyncMock(
        return_value=(
            "Noté.\n"
            '<meta>{"intent":"task","store_memory":false,"memory_content":null,'
            '"task":{"content":"acheter du pain","due_str":null},'
            '"feed":{"action":null,"name":null,"url":null},'
            '"event":{"action":null,"title":null,"start_str":null,"end_str":null,'
            '"location":null,"description":null,"range_str":null,"calendar_name":null},'
            '"fuel":{"fuel_type":null,"radius_km":null,"location":null},'
            '"weather":{"location":null,"when":null},"search_query":null}</meta>'
        )
    )
    response = await client.post(
        "/ask",
        headers={"X-API-Key": API_KEY},
        json={"message": "ajoute acheter du pain"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "task"
    assert "today_tasks" in body["refresh_cards"]
    assert "unread_notifications" in body["refresh_cards"]


async def test_ask_expense_intent_refreshes_budget_card(
    client: AsyncClient, state: AppState
) -> None:
    """Une saisie expense doit signaler au front que la card budget a bougé."""
    fake_expense = MagicMock()
    fake_expense.id = 42
    state.deps.expenses.add_punctual = AsyncMock(return_value=fake_expense)
    state.deps.llm.call = AsyncMock(
        return_value=(
            "Noté.\n"
            '<meta>{"intent":"expense","store_memory":false,"memory_content":null,'
            '"task":{"content":null,"due_str":null},'
            '"feed":{"action":null,"name":null,"url":null},'
            '"event":{"action":null,"title":null,"start_str":null,"end_str":null,'
            '"location":null,"description":null,"range_str":null,"calendar_name":null},'
            '"fuel":{"fuel_type":null,"radius_km":null,"location":null},'
            '"weather":{"location":null,"when":null},'
            '"depot":{"content":null,"kind":null},'
            '"expense":{"action":"spend","amount":27,"label":"pharmacie",'
            '"category":"santé","recurring_key":null,"when":null},'
            '"search_query":null}</meta>'
        )
    )
    response = await client.post(
        "/ask",
        headers={"X-API-Key": API_KEY},
        json={"message": "27€ pharmacie"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "expense"
    assert body["refresh_cards"] == ["budget"]


async def test_ask_depot_intent_refreshes_foryou_card(client: AsyncClient, state: AppState) -> None:
    """Un dépôt cognitif doit signaler au front que la card « Pour toi » a bougé."""
    fake_thought = MagicMock()
    fake_thought.id = 7
    fake_thought.content = "j'ai peur pour les finances"
    fake_thought.kind = "worry"
    state.deps.thoughts.create = AsyncMock(return_value=fake_thought)
    state.deps.thoughts.list_since = AsyncMock(return_value=[])
    state.deps.memory.store_depot = AsyncMock()
    state.deps.memory.find_similar_depots = AsyncMock(return_value=[])
    state.deps.settings.foryou_similarity_max_distance = 0.25
    state.deps.llm.call = AsyncMock(
        return_value=(
            "Noté.\n"
            '<meta>{"intent":"depot","store_memory":false,"memory_content":null,'
            '"task":{"content":null,"due_str":null},'
            '"feed":{"action":null,"name":null,"url":null},'
            '"event":{"action":null,"title":null,"start_str":null,"end_str":null,'
            '"location":null,"description":null,"range_str":null,"calendar_name":null},'
            '"fuel":{"fuel_type":null,"radius_km":null,"location":null},'
            '"weather":{"location":null,"when":null},'
            '"depot":{"content":"j\'ai peur pour les finances","kind":"worry"},'
            '"search_query":null}</meta>'
        )
    )
    response = await client.post(
        "/ask",
        headers={"X-API-Key": API_KEY},
        json={"message": "j'ai peur pour les finances"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "depot"
    assert body["refresh_cards"] == ["foryou"]


# --- GET /foryou ------------------------------------------------------------


async def test_foryou_without_api_key_returns_403(client: AsyncClient) -> None:
    response = await client.get("/foryou")
    assert response.status_code == 403


async def test_foryou_returns_items_and_fetched_at(client: AsyncClient, state: AppState) -> None:
    from datetime import UTC, datetime

    from bot.thoughts.foryou import ForYouItem, ForYouResult

    state.deps.foryou.build = AsyncMock(
        return_value=ForYouResult(
            items=[ForYouItem(type="closable_worry", message="C'est réglé ?", thought_ids=(12,))],
            fetched_at=datetime(2026, 6, 2, 14, 30, tzinfo=UTC),
        )
    )
    response = await client.get("/foryou", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == [
        {"type": "closable_worry", "message": "C'est réglé ?", "thought_ids": [12]}
    ]
    assert body["fetched_at"] == "2026-06-02T14:30:00+00:00"


async def test_foryou_never_500_when_build_raises(client: AsyncClient, state: AppState) -> None:
    """Une exception inattendue de l'orchestrateur → card vide, jamais de 500."""
    state.deps.foryou.build = AsyncMock(side_effect=RuntimeError("boom"))
    response = await client.get("/foryou", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_ask_without_x_source_uses_default_mode(client: AsyncClient, state: AppState) -> None:
    """Sans header X-Source, le pipeline reçoit voice_mode=False par défaut."""
    from unittest.mock import patch

    with patch("bot.api.process_message", new=AsyncMock(return_value=("ok", _NEUTRAL_META))) as pm:
        await client.post("/ask", headers={"X-API-Key": API_KEY}, json={"message": "salut"})
    assert pm.await_args.kwargs.get("voice_mode") is False


async def test_ask_with_x_source_siri_activates_voice_mode(
    client: AsyncClient, state: AppState
) -> None:
    """Header X-Source: siri → process_message reçoit voice_mode=True."""
    from unittest.mock import patch

    with patch("bot.api.process_message", new=AsyncMock(return_value=("ok", _NEUTRAL_META))) as pm:
        await client.post(
            "/ask",
            headers={"X-API-Key": API_KEY, "X-Source": "siri"},
            json={"message": "salut"},
        )
    assert pm.await_args.kwargs.get("voice_mode") is True


async def test_ask_with_other_x_source_does_not_activate_voice(
    client: AsyncClient, state: AppState
) -> None:
    """Une valeur X-Source inconnue (ex: watch) ne déclenche pas voice_mode."""
    from unittest.mock import patch

    with patch("bot.api.process_message", new=AsyncMock(return_value=("ok", _NEUTRAL_META))) as pm:
        await client.post(
            "/ask",
            headers={"X-API-Key": API_KEY, "X-Source": "watch"},
            json={"message": "salut"},
        )
    assert pm.await_args.kwargs.get("voice_mode") is False


async def test_ask_llm_timeout_returns_friendly_message(
    client: AsyncClient, state: AppState
) -> None:
    from bot.llm.client import LLMTimeoutError

    state.deps.llm.call = AsyncMock(side_effect=LLMTimeoutError("slow"))
    response = await client.post(
        "/ask",
        headers={"X-API-Key": API_KEY},
        json={"message": "salut"},
    )
    assert response.status_code == 200
    assert "trop longtemps" in response.json()["response"]


async def test_ask_llm_error_returns_friendly_message(client: AsyncClient, state: AppState) -> None:
    from bot.llm.client import LLMError

    state.deps.llm.call = AsyncMock(side_effect=LLMError("down"))
    response = await client.post(
        "/ask",
        headers={"X-API-Key": API_KEY},
        json={"message": "salut"},
    )
    assert response.status_code == 200
    assert "LLM" in response.json()["response"]


# --- /ask/stream ------------------------------------------------------------


def _sse_events(body: str) -> list[dict[str, object]]:
    """Parse les frames SSE `data: {json}\\n\\n` d'une réponse complète."""
    import json

    return [
        json.loads(frame[len("data: ") :])
        for frame in body.split("\n\n")
        if frame.startswith("data: ")
    ]


async def test_ask_stream_without_api_key_returns_403(client: AsyncClient) -> None:
    response = await client.post("/ask/stream", json={"message": "salut"})
    assert response.status_code == 403


async def test_ask_stream_emits_sse_frames(client: AsyncClient) -> None:
    from unittest.mock import patch

    async def fake_stream(message: str, deps: BotDeps) -> AsyncIterator[dict[str, object]]:
        yield {"type": "delta", "text": "Bon"}
        yield {"type": "delta", "text": "jour"}
        yield {"type": "done", "meta": _NEUTRAL_META}

    with patch("bot.api.process_message_stream", new=fake_stream):
        response = await client.post(
            "/ask/stream",
            headers={"X-API-Key": API_KEY},
            json={"message": "salut"},
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(response.text)
    assert [e["type"] for e in events] == ["delta", "delta", "done"]
    assert events[0]["text"] == "Bon"
    assert events[-1]["intent"] == "answer"
    assert events[-1]["refresh_cards"] == []


async def test_ask_stream_task_intent_lists_refresh_cards(client: AsyncClient) -> None:
    from unittest.mock import patch

    task_meta = dict(_NEUTRAL_META)
    task_meta["intent"] = "task"

    async def fake_stream(message: str, deps: BotDeps) -> AsyncIterator[dict[str, object]]:
        yield {"type": "delta", "text": "Noté."}
        yield {"type": "done", "meta": task_meta}

    with patch("bot.api.process_message_stream", new=fake_stream):
        response = await client.post(
            "/ask/stream",
            headers={"X-API-Key": API_KEY},
            json={"message": "rappelle-moi le pain"},
        )
    events = _sse_events(response.text)
    assert events[-1]["refresh_cards"] == ["today_tasks", "unread_notifications"]


async def test_ask_stream_llm_timeout_emits_error_frame(client: AsyncClient) -> None:
    from unittest.mock import patch

    from bot.llm.client import LLMTimeoutError

    async def fake_stream(message: str, deps: BotDeps) -> AsyncIterator[dict[str, object]]:
        yield {"type": "delta", "text": "déb"}
        raise LLMTimeoutError("slow")

    with patch("bot.api.process_message_stream", new=fake_stream):
        response = await client.post(
            "/ask/stream",
            headers={"X-API-Key": API_KEY},
            json={"message": "salut"},
        )
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[-1]["type"] == "error"
    assert "trop longtemps" in str(events[-1]["text"])


async def test_ask_stream_llm_error_emits_error_frame(client: AsyncClient) -> None:
    from unittest.mock import patch

    from bot.llm.client import LLMError

    async def fake_stream(message: str, deps: BotDeps) -> AsyncIterator[dict[str, object]]:
        raise LLMError("down")
        yield  # pragma: no cover — fait de la fonction un générateur async

    with patch("bot.api.process_message_stream", new=fake_stream):
        response = await client.post(
            "/ask/stream",
            headers={"X-API-Key": API_KEY},
            json={"message": "salut"},
        )
    events = _sse_events(response.text)
    assert [e["type"] for e in events] == ["error"]
    assert "LLM" in str(events[0]["text"])


# --- /ask/image -------------------------------------------------------------


async def test_ask_image_decodes_base64_and_calls_pipeline(
    client: AsyncClient, state: AppState
) -> None:
    payload = {
        "message": "décris cette photo",
        "image_b64": base64.b64encode(b"fake-image-bytes").decode("ascii"),
        "media_type": "image/jpeg",
    }
    response = await client.post(
        "/ask/image",
        headers={"X-API-Key": API_KEY},
        json=payload,
    )
    assert response.status_code == 200
    state.deps.llm.call.assert_awaited_once()
    kwargs = state.deps.llm.call.await_args.kwargs
    assert kwargs["images"] == [b"fake-image-bytes"]


async def test_ask_image_expense_returns_draft_not_written(
    client: AsyncClient, state: AppState
) -> None:
    """Une capture lue comme dépense renvoie un brouillon, sans rien écrire.

    refresh_cards reste vide (rien n'a bougé tant que l'utilisateur n'a pas
    validé via POST /expenses), et la date FR est résolue en ISO.
    """
    from bot.pipeline import parse_when_to_date

    expense_meta = {
        **_NEUTRAL_META,
        "intent": "expense",
        "expense": {
            "action": "spend",
            "amount": 23.4,
            "label": "Lidl",
            "category": "courses",
            "recurring_key": None,
            "when": None,
            "shared": False,
            "starts_cycle": False,
        },
    }
    payload = {
        "message": "",
        "image_b64": base64.b64encode(b"revolut-screenshot").decode("ascii"),
        "media_type": "image/png",
    }
    with patch(
        "bot.api.process_message",
        new=AsyncMock(return_value=("J'ai lu cette dépense, vérifie-la.", expense_meta)),
    ):
        response = await client.post("/ask/image", headers={"X-API-Key": API_KEY}, json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["refresh_cards"] == []
    draft = body["expense_draft"]
    assert draft is not None
    assert draft["action"] == "spend"
    assert draft["amount_eur"] == 23.4
    assert draft["label"] == "Lidl"
    assert draft["category"] == "courses"
    assert draft["shared"] is False
    assert draft["occurred_on"] == parse_when_to_date(None, "Europe/Paris").isoformat()
    # Aucune écriture côté ExpenseManager : la confirmation passera par /expenses.
    state.deps.expenses.add_punctual.assert_not_called()


async def test_ask_image_recurring_returns_tick_draft(client: AsyncClient, state: AppState) -> None:
    """Une transaction reconnue comme récurrente (Netflix) → brouillon tick_recurring.

    Le brouillon porte recurring_key pour que le formulaire s'ouvre déjà réglé
    sur « Pointer une récurrente » (évite le double comptage). Rien n'est écrit.
    """
    recurring_meta = {
        **_NEUTRAL_META,
        "intent": "expense",
        "expense": {
            "action": "tick_recurring",
            "amount": 17.99,
            "label": "Netflix",
            "category": None,
            "recurring_key": "netflix",
            "when": None,
            "shared": False,
            "starts_cycle": False,
        },
    }
    payload = {
        "message": "",
        "image_b64": base64.b64encode(b"revolut-netflix").decode("ascii"),
        "media_type": "image/png",
    }
    with patch(
        "bot.api.process_message",
        new=AsyncMock(return_value=("J'ai lu cette dépense, vérifie-la.", recurring_meta)),
    ):
        response = await client.post("/ask/image", headers={"X-API-Key": API_KEY}, json=payload)
    assert response.status_code == 200
    draft = response.json()["expense_draft"]
    assert draft["action"] == "tick_recurring"
    assert draft["recurring_key"] == "netflix"
    assert draft["amount_eur"] == 17.99
    state.deps.expenses.tick_recurring_once.assert_not_called()


async def test_ask_image_non_expense_has_no_draft(client: AsyncClient, state: AppState) -> None:
    """Une photo non-financière garde le comportement actuel (pas de draft)."""
    payload = {
        "message": "décris cette photo",
        "image_b64": base64.b64encode(b"dog-photo").decode("ascii"),
        "media_type": "image/jpeg",
    }
    with patch(
        "bot.api.process_message",
        new=AsyncMock(return_value=("Un chien.", _NEUTRAL_META)),
    ):
        response = await client.post("/ask/image", headers={"X-API-Key": API_KEY}, json=payload)
    assert response.status_code == 200
    assert response.json()["expense_draft"] is None


async def test_ask_image_rejects_invalid_base64(client: AsyncClient) -> None:
    response = await client.post(
        "/ask/image",
        headers={"X-API-Key": API_KEY},
        json={
            "message": "x",
            "image_b64": "not-valid-base64!!!",
            "media_type": "image/jpeg",
        },
    )
    assert response.status_code == 400


async def test_ask_image_rejects_oversized_payload(client: AsyncClient, state: AppState) -> None:
    # Au-delà de MAX_IMAGE_B64_CHARS : rejet Pydantic (422) avant tout décodage.
    response = await client.post(
        "/ask/image",
        headers={"X-API-Key": API_KEY},
        json={
            "message": "décris",
            "image_b64": "A" * 20_000_001,
            "media_type": "image/jpeg",
        },
    )
    assert response.status_code == 422
    state.deps.llm.call.assert_not_awaited()


# --- /notifications ---------------------------------------------------------


async def test_notifications_empty_returns_empty_list(client: AsyncClient) -> None:
    response = await client.get("/notifications", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    assert response.json() == {"notifications": []}


async def test_notifications_returns_then_marks_read(client: AsyncClient, state: AppState) -> None:
    await state.notifications.add("Premier rappel")
    await state.notifications.add("Deuxième rappel")

    response = await client.get("/notifications", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    body = response.json()
    assert [n["text"] for n in body["notifications"]] == ["Premier rappel", "Deuxième rappel"]

    # Deuxième poll : la file doit être vide (entrées déjà marquées comme lues).
    response_again = await client.get("/notifications", headers={"X-API-Key": API_KEY})
    assert response_again.json() == {"notifications": []}


# --- UI web -----------------------------------------------------------------


async def test_chat_ui_returns_html(client: AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Copain" in response.text


async def test_get_config(client: AsyncClient) -> None:
    response = await client.get("/config")
    assert response.status_code == 200
    assert "api_key" in response.json()


# --- /dashboard -------------------------------------------------------------


async def test_dashboard_without_api_key_returns_403(client: AsyncClient) -> None:
    response = await client.get("/dashboard")
    assert response.status_code == 403


async def test_dashboard_does_not_consume_unread_notifications(
    client: AsyncClient, state: AppState
) -> None:
    """`GET /dashboard` doit pouvoir être appelé à volonté sans purger la file."""
    from bot.weather.client import WeatherError

    state.deps.tasks.list_pending = AsyncMock(return_value=[])
    state.deps.weather.get_today = AsyncMock(side_effect=WeatherError("down"))
    state.deps.calendar.is_connected = False

    await state.notifications.add("Une notif")
    await state.notifications.add("Une autre")

    dashboard_resp = await client.get("/dashboard", headers={"X-API-Key": API_KEY})
    assert dashboard_resp.status_code == 200
    assert dashboard_resp.json()["unread_notifications"] == 2

    # Après un GET /dashboard, /notifications doit toujours pouvoir consommer les 2 notifs.
    notifs_resp = await client.get("/notifications", headers={"X-API-Key": API_KEY})
    assert notifs_resp.status_code == 200
    assert len(notifs_resp.json()["notifications"]) == 2


async def test_dashboard_tolerates_weather_and_calendar_down(
    client: AsyncClient, state: AppState
) -> None:
    """Météo + calendar down → cards null, autres sections renvoyées."""
    from bot.weather.client import WeatherError

    state.deps.weather.get_today = AsyncMock(side_effect=WeatherError("api down"))
    state.deps.calendar.is_connected = False
    state.deps.tasks.list_pending = AsyncMock(return_value=[])

    response = await client.get("/dashboard", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    body = response.json()
    assert body["weather"] is None
    assert body["next_event"] is None
    assert body["today_tasks"] == []
    assert body["unread_notifications"] == 0


# --- /event/location -------------------------------------------------------


async def test_location_event_requires_api_key(client: AsyncClient) -> None:
    response = await client.post("/event/location", json={"event": "arrived", "place": "home"})
    assert response.status_code == 403


async def test_location_event_rejects_invalid_event_value(client: AsyncClient) -> None:
    response = await client.post(
        "/event/location",
        headers={"X-API-Key": API_KEY},
        json={"event": "foo", "place": "home"},
    )
    assert response.status_code == 422


async def test_location_event_rejects_out_of_range_coords(client: AsyncClient) -> None:
    """lat hors [-90, 90] ou lon hors [-180, 180] → 422."""
    for coords in ({"lat": 91.0, "lon": 7.45}, {"lat": 48.26, "lon": -181.0}):
        response = await client.post(
            "/event/location",
            headers={"X-API-Key": API_KEY},
            json={"event": "arrived", "place": "home", **coords},
        )
        assert response.status_code == 422


async def test_location_event_records_and_returns_current_place(
    client: AsyncClient, state: AppState
) -> None:
    response = await client.post(
        "/event/location",
        headers={"X-API-Key": API_KEY},
        json={"event": "arrived", "place": "home"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {"recorded": True, "current_place": "home"}

    # Vérifie qu'un second event "left" remet current_place à None.
    response_left = await client.post(
        "/event/location",
        headers={"X-API-Key": API_KEY},
        json={"event": "left", "place": "home"},
    )
    assert response_left.json() == {"recorded": True, "current_place": None}


async def test_location_event_accepts_coords_and_at_timestamp(
    client: AsyncClient, state: AppState
) -> None:
    response = await client.post(
        "/event/location",
        headers={"X-API-Key": API_KEY},
        json={
            "event": "arrived",
            "place": "work",
            "lat": 48.46,
            "lon": 7.48,
            "at": "2026-05-12T08:30:00+02:00",
        },
    )
    assert response.status_code == 200
    presence = await state.deps.location_events.get_current_location()
    assert presence is not None
    assert presence.place == "work"
    assert presence.lat == pytest.approx(48.46)
    assert presence.lon == pytest.approx(7.48)


async def test_location_event_triggers_proactivity(client: AsyncClient, state: AppState) -> None:
    """Chaque event de localisation doit déclencher proactivity.on_location_event."""
    response = await client.post(
        "/event/location",
        headers={"X-API-Key": API_KEY},
        json={"event": "left", "place": "work"},
    )
    assert response.status_code == 200
    state.deps.proactivity.on_location_event.assert_awaited_once_with("left", "work")


# --- /tasks (list / complete / delete) -------------------------------------


async def test_tasks_list_requires_api_key(client: AsyncClient) -> None:
    response = await client.get("/tasks")
    assert response.status_code == 403


async def test_tasks_list_returns_pending_tasks(client: AsyncClient, state: AppState) -> None:
    from datetime import UTC, datetime

    fake_t1 = MagicMock()
    fake_t1.id = 1
    fake_t1.content = "acheter du pain"
    fake_t1.due_at = None
    fake_t2 = MagicMock()
    fake_t2.id = 2
    fake_t2.content = "appeler dentiste"
    fake_t2.due_at = datetime(2026, 5, 13, 14, 0, tzinfo=UTC)

    state.deps.tasks.list_pending = AsyncMock(return_value=[fake_t2, fake_t1])

    response = await client.get("/tasks", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    body = response.json()
    assert len(body["tasks"]) == 2
    assert body["tasks"][0]["id"] == 2
    assert body["tasks"][0]["content"] == "appeler dentiste"
    assert body["tasks"][0]["due_at"] is not None
    assert body["tasks"][1]["due_at"] is None


async def test_complete_task_marks_as_done(client: AsyncClient, state: AppState) -> None:
    state.deps.tasks.complete = AsyncMock(return_value=True)
    response = await client.post("/tasks/42/complete", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    state.deps.tasks.complete.assert_awaited_once_with(42)


async def test_complete_unknown_task_returns_404(client: AsyncClient, state: AppState) -> None:
    state.deps.tasks.complete = AsyncMock(return_value=False)
    response = await client.post("/tasks/999/complete", headers={"X-API-Key": API_KEY})
    assert response.status_code == 404


async def test_delete_task_removes_it(client: AsyncClient, state: AppState) -> None:
    state.deps.tasks.delete = AsyncMock(return_value=True)
    response = await client.delete("/tasks/42", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    state.deps.tasks.delete.assert_awaited_once_with(42)


async def test_delete_unknown_task_returns_404(client: AsyncClient, state: AppState) -> None:
    state.deps.tasks.delete = AsyncMock(return_value=False)
    response = await client.delete("/tasks/999", headers={"X-API-Key": API_KEY})
    assert response.status_code == 404


async def test_complete_task_requires_api_key(client: AsyncClient) -> None:
    response = await client.post("/tasks/1/complete")
    assert response.status_code == 403


async def test_delete_task_requires_api_key(client: AsyncClient) -> None:
    response = await client.delete("/tasks/1")
    assert response.status_code == 403


# --- /expenses/export.csv -----------------------------------------------


async def test_expenses_export_csv_requires_api_key(client: AsyncClient) -> None:
    response = await client.get("/expenses/export.csv?from=2026-05-01&to=2026-05-31")
    assert response.status_code == 403


async def test_expenses_export_csv_returns_attachment_with_bom_and_header(
    client: AsyncClient, state: AppState
) -> None:
    from datetime import date

    from bot.finance.models import Expense

    rows = [
        Expense(
            kind="income",
            amount_cents=250000,
            label="Salaire mai",
            category=None,
            recurring_key=None,
            occurred_on=date(2026, 5, 1),
        ),
        Expense(
            kind="punctual",
            amount_cents=2750,
            label="Pharmacie",
            category="santé",
            recurring_key=None,
            occurred_on=date(2026, 5, 18),
        ),
    ]
    state.deps.expenses.list_between = AsyncMock(return_value=rows)

    response = await client.get(
        "/expenses/export.csv?from=2026-05-01&to=2026-05-31",
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition
    assert "copain-depenses-2026-05-01_2026-05-31.csv" in disposition

    body = response.content.decode("utf-8")
    assert body.startswith("﻿")  # BOM UTF-8
    lines = body.removeprefix("﻿").splitlines()
    assert lines[0] == "date;type;libelle;categorie;recurring_key;montant_eur"
    assert "01/05/2026;income;Salaire mai;;;2500,00" in lines
    assert "18/05/2026;punctual;Pharmacie;santé;;-27,50" in lines

    state.deps.expenses.list_between.assert_awaited_once_with(date(2026, 5, 1), date(2026, 5, 31))


async def test_expenses_export_csv_rejects_invalid_dates(
    client: AsyncClient, state: AppState
) -> None:
    state.deps.expenses.list_between = AsyncMock(return_value=[])
    response = await client.get(
        "/expenses/export.csv?from=pas-une-date&to=2026-05-31",
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 400


async def test_expenses_export_csv_rejects_inverted_range(
    client: AsyncClient, state: AppState
) -> None:
    state.deps.expenses.list_between = AsyncMock(return_value=[])
    response = await client.get(
        "/expenses/export.csv?from=2026-06-01&to=2026-05-01",
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 400


async def test_expenses_export_csv_empty_returns_header_only(
    client: AsyncClient, state: AppState
) -> None:
    state.deps.expenses.list_between = AsyncMock(return_value=[])
    response = await client.get(
        "/expenses/export.csv?from=2026-05-01&to=2026-05-31",
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 200
    body = response.content.decode("utf-8").removeprefix("﻿")
    assert body.splitlines() == ["date;type;libelle;categorie;recurring_key;montant_eur"]


# --- /weather/forecast et /events ----------------------------------------


async def test_weather_forecast_requires_api_key(client: AsyncClient) -> None:
    response = await client.get("/weather/forecast")
    assert response.status_code == 403


async def test_weather_forecast_returns_hourly_and_daily(
    client: AsyncClient, state: AppState
) -> None:
    from datetime import UTC, date, datetime

    from bot.weather.client import DailyWeather, HourlyForecast

    state.deps.weather.get_hourly_forecast = AsyncMock(
        return_value=[
            HourlyForecast(
                time=datetime(2026, 5, 12, 15, 0, tzinfo=UTC),
                temp_c=16.0,
                precipitation_mm=0.0,
                precipitation_probability_pct=10,
                description="ciel dégagé",
            )
        ]
    )
    state.deps.weather.get_forecast = AsyncMock(
        return_value=[
            DailyWeather(
                city="Sélestat",
                date=date(2026, 5, 12),
                temp_min=10.0,
                temp_max=20.0,
                precipitation_mm=0.0,
                wind_kmh_max=12.0,
                description="ciel dégagé",
                temp_current=16.0,
            )
        ]
    )

    response = await client.get("/weather/forecast", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    body = response.json()
    assert body["city"] == "Sélestat"
    assert len(body["hourly"]) == 1
    assert body["hourly"][0]["temp_c"] == 16.0
    assert len(body["daily"]) == 1
    assert body["daily"][0]["temp_min"] == 10.0


async def test_weather_forecast_uses_work_when_at_work(
    client: AsyncClient, state: AppState
) -> None:
    """current_location.place=work → coords WORK_* utilisées."""
    from datetime import UTC, datetime

    from bot.locations.presence import LocationPresence

    presence = LocationPresence(
        place="work",
        arrived_at=datetime.now(UTC),
        lat=None,
        lon=None,
    )
    state.deps.location_events.get_current_location = AsyncMock(return_value=presence)
    state.deps.settings.work_lat = 48.46
    state.deps.settings.work_lon = 7.48
    state.deps.settings.work_city = "Obernai"
    state.deps.weather.get_hourly_forecast = AsyncMock(return_value=[])
    state.deps.weather.get_forecast = AsyncMock(return_value=[])

    response = await client.get("/weather/forecast", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    assert response.json()["city"] == "Obernai"
    hourly_kwargs = state.deps.weather.get_hourly_forecast.await_args.kwargs
    assert hourly_kwargs["lat"] == 48.46
    assert hourly_kwargs["lon"] == 7.48


async def test_events_requires_api_key(client: AsyncClient) -> None:
    response = await client.get("/events")
    assert response.status_code == 403


async def test_events_returns_upcoming_list(client: AsyncClient, state: AppState) -> None:
    from datetime import UTC, datetime, timedelta

    from bot.calendar.models import CalendarEvent

    now = datetime.now(UTC)
    ev = CalendarEvent(
        uid="evt-1",
        title="Réunion équipe",
        start=now + timedelta(hours=2),
        end=now + timedelta(hours=3),
        location="Bureau",
        description=None,
        calendar_name="Pro",
    )
    state.deps.calendar.is_connected = True
    state.deps.calendar.list_all_upcoming = AsyncMock(return_value=[ev])

    response = await client.get("/events", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == 1
    assert body["events"][0]["title"] == "Réunion équipe"
    assert body["events"][0]["location"] == "Bureau"


async def test_events_empty_when_calendar_disconnected(
    client: AsyncClient, state: AppState
) -> None:
    state.deps.calendar.is_connected = False
    response = await client.get("/events", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    assert response.json()["events"] == []


# --- /news/latest ---------------------------------------------------------


async def test_news_latest_requires_api_key(client: AsyncClient) -> None:
    response = await client.get("/news/latest")
    assert response.status_code == 403


async def test_news_latest_empty_profile_returns_hint(client: AsyncClient, state: AppState) -> None:
    """Profil sans `news_topics.daily_briefing` → message d'aide, pas d'appel SearXNG."""
    state.deps.news.fetch_top_news = AsyncMock()
    response = await client.get("/news/latest", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    body = response.json()
    assert "profile.yaml" in body["markdown"]
    state.deps.news.fetch_top_news.assert_not_called()


async def test_news_latest_calls_curator(client: AsyncClient, state: AppState) -> None:
    state.deps.profile = UserProfile(
        raw_yaml="",
        is_loaded=True,
        data={
            "news_topics": {
                "daily_briefing": ["LLM agents", "OpenAI"],
                "filters": {"domains_blocklist": ["reddit.com"]},
            }
        },
    )
    state.deps.news.fetch_top_news = AsyncMock(return_value="**Actu 1**\n**Actu 2**")
    response = await client.get("/news/latest", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    body = response.json()
    assert "Actu 1" in body["markdown"]
    assert "fetched_at" in body
    kwargs = state.deps.news.fetch_top_news.await_args.kwargs
    assert kwargs["topics"] == ["LLM agents", "OpenAI"]
    assert kwargs["domains_blocklist"] == ["reddit.com"]


async def test_news_latest_curator_failure_returns_502(
    client: AsyncClient, state: AppState
) -> None:
    state.deps.profile = UserProfile(
        raw_yaml="", is_loaded=True, data={"news_topics": {"daily_briefing": ["AI"]}}
    )
    state.deps.news.fetch_top_news = AsyncMock(side_effect=RuntimeError("searxng down"))
    response = await client.get("/news/latest", headers={"X-API-Key": API_KEY})
    assert response.status_code == 502


# --- /thoughts -------------------------------------------------------------


async def test_thoughts_requires_api_key(client: AsyncClient) -> None:
    response = await client.get("/thoughts")
    assert response.status_code == 403


async def test_thoughts_returns_recent_list(client: AsyncClient, state: AppState) -> None:
    from datetime import UTC, datetime

    fake_t1 = MagicMock()
    fake_t1.id = 2
    fake_t1.content = "j'ai peur pour les finances"
    fake_t1.kind = "worry"
    fake_t1.created_at = datetime(2026, 5, 18, 10, 30, tzinfo=UTC)

    fake_t2 = MagicMock()
    fake_t2.id = 1
    fake_t2.content = "idée : refactorer le pipeline"
    fake_t2.kind = "idea"
    fake_t2.created_at = datetime(2026, 5, 17, 14, 0, tzinfo=UTC)

    state.deps.thoughts.list_recent = AsyncMock(return_value=[fake_t1, fake_t2])

    response = await client.get("/thoughts", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    body = response.json()
    assert len(body["thoughts"]) == 2
    assert body["thoughts"][0]["id"] == 2
    assert body["thoughts"][0]["kind"] == "worry"
    assert body["thoughts"][1]["kind"] == "idea"


async def test_thoughts_with_since_filter(client: AsyncClient, state: AppState) -> None:
    state.deps.thoughts.list_since = AsyncMock(return_value=[])
    response = await client.get(
        "/thoughts?since=2026-05-15T00:00:00",
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 200
    state.deps.thoughts.list_since.assert_awaited_once()
    args, kwargs = state.deps.thoughts.list_since.await_args
    since_arg = args[0] if args else kwargs.get("since")
    assert since_arg.year == 2026
    assert since_arg.month == 5
    assert since_arg.day == 15


async def test_thoughts_rejects_invalid_since(client: AsyncClient, state: AppState) -> None:
    response = await client.get(
        "/thoughts?since=not-a-date",
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 400


async def test_thoughts_clamps_excessive_limit(client: AsyncClient, state: AppState) -> None:
    state.deps.thoughts.list_recent = AsyncMock(return_value=[])
    response = await client.get(
        "/thoughts?limit=99999",
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 200
    kwargs = state.deps.thoughts.list_recent.await_args.kwargs
    assert kwargs["limit"] == 200


# --- POST /thoughts/{id}/close ----------------------------------------------


async def test_close_thought_returns_closed(client: AsyncClient, state: AppState) -> None:
    state.deps.thoughts.close = AsyncMock(return_value=True)
    response = await client.post("/thoughts/12/close", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    assert response.json() == {"closed": True, "thought_id": 12}
    state.deps.thoughts.close.assert_awaited_once_with(12)


async def test_close_thought_is_idempotent(client: AsyncClient, state: AppState) -> None:
    # Le manager retourne True même pour un dépôt déjà clos : 200 idem.
    state.deps.thoughts.close = AsyncMock(return_value=True)
    for _ in range(2):
        response = await client.post("/thoughts/12/close", headers={"X-API-Key": API_KEY})
        assert response.status_code == 200
        assert response.json() == {"closed": True, "thought_id": 12}


async def test_close_unknown_thought_returns_404(client: AsyncClient, state: AppState) -> None:
    state.deps.thoughts.close = AsyncMock(return_value=False)
    response = await client.post("/thoughts/999/close", headers={"X-API-Key": API_KEY})
    assert response.status_code == 404


async def test_close_thought_requires_api_key(client: AsyncClient) -> None:
    response = await client.post("/thoughts/1/close")
    assert response.status_code == 403


# --- POST /thoughts (dépôt express, formulaire sans LLM) --------------------


async def test_create_thought_requires_api_key(client: AsyncClient) -> None:
    response = await client.post("/thoughts", json={"content": "une pensée"})
    assert response.status_code == 403


async def test_create_thought_records_and_returns_ack(client: AsyncClient, state: AppState) -> None:
    """Dépôt direct nominal : persiste via record_depot et renvoie un accusé sobre."""
    from datetime import UTC, datetime

    fake_thought = MagicMock()
    fake_thought.id = 9
    fake_thought.content = "penser à rappeler le dentiste"
    fake_thought.kind = "note"
    fake_thought.created_at = datetime(2026, 6, 30, 9, 15, tzinfo=UTC)
    state.deps.thoughts.create = AsyncMock(return_value=fake_thought)
    state.deps.thoughts.list_since = AsyncMock(return_value=[])
    state.deps.memory.store_depot = AsyncMock()
    state.deps.memory.find_similar_depots = AsyncMock(return_value=[])
    state.deps.settings.foryou_similarity_max_distance = 0.25

    response = await client.post(
        "/thoughts",
        headers={"X-API-Key": API_KEY},
        json={"content": "penser à rappeler le dentiste", "kind": "note"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["recorded"] is True
    assert body["thought"]["id"] == 9
    assert body["thought"]["kind"] == "note"
    assert body["ack"] == "C'est posé."
    state.deps.thoughts.create.assert_awaited_once_with(
        content="penser à rappeler le dentiste", kind="note"
    )


async def test_create_thought_accepts_null_kind(client: AsyncClient, state: AppState) -> None:
    from datetime import UTC, datetime

    fake_thought = MagicMock()
    fake_thought.id = 10
    fake_thought.content = "vidage de tête"
    fake_thought.kind = None
    fake_thought.created_at = datetime(2026, 6, 30, 9, 15, tzinfo=UTC)
    state.deps.thoughts.create = AsyncMock(return_value=fake_thought)
    state.deps.thoughts.list_since = AsyncMock(return_value=[])
    state.deps.memory.store_depot = AsyncMock()
    state.deps.memory.find_similar_depots = AsyncMock(return_value=[])
    state.deps.settings.foryou_similarity_max_distance = 0.25

    response = await client.post(
        "/thoughts",
        headers={"X-API-Key": API_KEY},
        json={"content": "vidage de tête"},
    )
    assert response.status_code == 200
    assert response.json()["thought"]["kind"] is None
    state.deps.thoughts.create.assert_awaited_once_with(content="vidage de tête", kind=None)


async def test_create_thought_empty_content_returns_400(
    client: AsyncClient, state: AppState
) -> None:
    state.deps.thoughts.create = AsyncMock()
    response = await client.post(
        "/thoughts",
        headers={"X-API-Key": API_KEY},
        json={"content": "   "},
    )
    assert response.status_code == 400
    state.deps.thoughts.create.assert_not_awaited()


async def test_create_thought_invalid_kind_returns_400(
    client: AsyncClient, state: AppState
) -> None:
    state.deps.thoughts.create = AsyncMock()
    response = await client.post(
        "/thoughts",
        headers={"X-API-Key": API_KEY},
        json={"content": "une pensée", "kind": "panique"},
    )
    assert response.status_code == 400
    state.deps.thoughts.create.assert_not_awaited()


async def test_create_thought_loop_ack_suffix(client: AsyncClient, state: AppState) -> None:
    """Une boucle de rumination détectée suffixe l'accusé comme le chemin bot."""
    from datetime import UTC, datetime
    from unittest.mock import patch

    fake_thought = MagicMock()
    fake_thought.id = 11
    fake_thought.content = "encore cette angoisse"
    fake_thought.kind = "worry"
    fake_thought.created_at = datetime(2026, 6, 30, 9, 15, tzinfo=UTC)
    with patch(
        "bot.api.record_depot",
        new=AsyncMock(return_value=(fake_thought, 3)),
    ):
        response = await client.post(
            "/thoughts",
            headers={"X-API-Key": API_KEY},
            json={"content": "encore cette angoisse", "kind": "worry"},
        )
    assert response.status_code == 200
    assert response.json()["ack"] == "C'est posé. — 3e fois que ça revient."


# --- /budget --------------------------------------------------------------


async def test_budget_requires_api_key(client: AsyncClient) -> None:
    response = await client.get("/budget")
    assert response.status_code == 403


async def test_budget_returns_empty_state_when_yaml_missing(
    client: AsyncClient, state: AppState
) -> None:
    state.deps.expenses.list_for_cycle = AsyncMock(return_value=[])
    state.deps.expenses.list_savings_for_year = AsyncMock(return_value=[])
    response = await client.get("/budget", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    body = response.json()
    assert body["currency"] == "EUR"
    assert body["income_eur"] == 0
    assert body["remaining_eur"] == 0
    assert body["pending"] == []


async def test_budget_returns_envelopes_with_overrun_flag(
    client: AsyncClient, state: AppState
) -> None:
    from datetime import date as _date

    from bot.finance.models import Expense
    from bot.profile import UserProfile

    state.deps.profile = UserProfile(
        raw_yaml="",
        is_loaded=True,
        data={
            "finances": {
                "envelopes": [
                    {"category": "essence", "label": "Essence", "amount": 200},
                ],
            }
        },
    )
    over = Expense(
        kind="punctual",
        amount_cents=23000,
        label="Essence Total",
        category="essence",
        recurring_key=None,
        occurred_on=_date(2026, 5, 10),
    )
    over.id = 7
    state.deps.expenses.list_for_cycle = AsyncMock(return_value=[over])
    state.deps.expenses.list_savings_for_year = AsyncMock(return_value=[])

    response = await client.get("/budget", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    body = response.json()
    assert len(body["envelopes"]) == 1
    env = body["envelopes"][0]
    assert env["category"] == "essence"
    assert env["allocated_eur"] == 200.0
    assert env["spent_eur"] == 230.0
    assert env["overrun_eur"] == 30.0
    assert env["is_overrun"] is True


async def test_budget_returns_summary_with_transactions(
    client: AsyncClient, state: AppState
) -> None:
    from datetime import date as _date

    from bot.finance.models import Expense
    from bot.profile import UserProfile

    state.deps.profile = UserProfile(
        raw_yaml="",
        is_loaded=True,
        data={
            "finances": {
                "currency": "EUR",
                "recurring": [
                    {
                        "key": "loyer",
                        "label": "Loyer",
                        "amount": 800,
                        "day": 5,
                        "kind": "expense",
                    }
                ],
            }
        },
    )
    income = Expense(
        kind="income",
        amount_cents=250000,
        label="Salaire",
        recurring_key=None,
        occurred_on=_date(2026, 5, 5),
    )
    income.id = 1
    state.deps.expenses.list_for_cycle = AsyncMock(return_value=[income])
    state.deps.expenses.list_savings_for_year = AsyncMock(return_value=[])
    response = await client.get("/budget", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    body = response.json()
    assert body["income_eur"] == 2500.0
    assert len(body["transactions"]) == 1
    assert len(body["pending"]) == 1
    assert body["pending"][0]["key"] == "loyer"


# --- /share/courses -------------------------------------------------------


async def test_share_courses_requires_api_key(client: AsyncClient) -> None:
    response = await client.get("/share/courses")
    assert response.status_code == 403


async def test_share_courses_404_when_no_courses_envelope(
    client: AsyncClient, state: AppState
) -> None:
    from bot.profile import UserProfile

    state.deps.profile = UserProfile(
        raw_yaml="",
        is_loaded=True,
        data={
            "finances": {"envelopes": [{"category": "essence", "label": "Essence", "amount": 200}]}
        },
    )
    state.deps.expenses.list_for_cycle = AsyncMock(return_value=[])
    state.deps.expenses.list_savings_for_year = AsyncMock(return_value=[])

    response = await client.get("/share/courses", headers={"X-API-Key": API_KEY})
    assert response.status_code == 404


async def test_share_courses_matches_shared_envelope_by_label(
    client: AsyncClient, state: AppState
) -> None:
    from datetime import date as _date

    from bot.finance.models import Expense
    from bot.profile import UserProfile

    # Reproduit la config réelle : l'enveloppe "courses" est category=nourriture,
    # label="Courses (compte joint)", shared=true. Le matching se fait sur "cours".
    state.deps.profile = UserProfile(
        raw_yaml="",
        is_loaded=True,
        data={
            "finances": {
                "envelopes": [
                    {"category": "essence", "label": "Essence", "amount": 200},
                    {
                        "category": "nourriture",
                        "label": "Courses (compte joint)",
                        "amount": 499,
                        "shared": True,
                    },
                ]
            }
        },
    )
    spent = Expense(
        kind="punctual",
        amount_cents=12050,
        label="Lidl",
        category="nourriture",
        recurring_key=None,
        occurred_on=_date(2026, 6, 10),
        shared=True,
    )
    spent.id = 11
    state.deps.expenses.list_for_cycle = AsyncMock(return_value=[spent])
    state.deps.expenses.list_savings_for_year = AsyncMock(return_value=[])

    response = await client.get("/share/courses", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    body = response.json()
    assert body["label"] == "Courses (compte joint)"
    assert body["allocated_eur"] == 499.0
    assert body["spent_eur"] == 120.5
    assert body["remaining_eur"] == 378.5
    assert body["is_overrun"] is False
    assert "378,50 €" in body["text"]
    assert "499 €" in body["text"]


# --- /dashboard suite -----------------------------------------------------


async def test_dashboard_populates_weather_when_available(
    client: AsyncClient, state: AppState
) -> None:
    from bot.weather.client import WeatherSummary

    state.deps.weather.get_today = AsyncMock(
        return_value=WeatherSummary(
            city="Sélestat",
            temp_current=16.0,
            temp_min=10.0,
            temp_max=20.0,
            precipitation_mm=0.0,
            wind_kmh=12.0,
            description="ciel dégagé",
        )
    )
    state.deps.calendar.is_connected = False
    state.deps.tasks.list_pending = AsyncMock(return_value=[])

    response = await client.get("/dashboard", headers={"X-API-Key": API_KEY})
    body = response.json()
    assert body["weather"] == {
        "city": "Sélestat",
        "temp_current": 16.0,
        "temp_min": 10.0,
        "temp_max": 20.0,
        "description": "ciel dégagé",
        "precipitation_mm": 0.0,
        "wind_kmh": 12.0,
    }


# --- GET /history (historique du mode dialogue) -----------------------------


async def test_history_without_api_key_returns_403(client: AsyncClient) -> None:
    response = await client.get("/history")
    assert response.status_code == 403


async def test_history_empty_when_disabled(client: AsyncClient) -> None:
    """chat_history=None (défaut des tests) → page vide, 200."""
    response = await client.get("/history", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    assert response.json() == {"messages": [], "has_more": False}


async def test_history_returns_seeded_messages(
    state: AppState, client: AsyncClient, engine: AsyncEngine
) -> None:
    mgr = ChatHistoryManager(engine)
    await mgr.init_schema()
    await mgr.add_exchange("salut", "bonjour à toi")
    state.deps.chat_history = mgr

    response = await client.get("/history", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    body = response.json()
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert [m["content"] for m in body["messages"]] == ["salut", "bonjour à toi"]
    assert body["has_more"] is False


async def test_history_paginates_with_before_id(
    state: AppState, client: AsyncClient, engine: AsyncEngine
) -> None:
    mgr = ChatHistoryManager(engine)
    await mgr.init_schema()
    for i in range(3):
        await mgr.add_exchange(f"q{i}", f"r{i}")  # 6 lignes, ids 1..6
    state.deps.chat_history = mgr

    first = await client.get("/history?limit=4", headers={"X-API-Key": API_KEY})
    body = first.json()
    assert body["has_more"] is True
    cursor = body["messages"][0]["id"]

    older = await client.get(f"/history?limit=4&before_id={cursor}", headers={"X-API-Key": API_KEY})
    older_body = older.json()
    assert older_body["has_more"] is False
    assert [m["content"] for m in older_body["messages"]] == ["q0", "r0"]


# --- POST /expenses (saisie directe par formulaire, sans LLM) ---------------


def _profile_with_recurring() -> UserProfile:
    """Profil minimal portant une récurrente `loyer` (pour les ticks)."""
    return UserProfile(
        raw_yaml="finances:\n  recurring:\n    - key: loyer",
        is_loaded=True,
        data={
            "finances": {
                "recurring": [
                    {"key": "loyer", "label": "Loyer", "amount": 800, "day": 5, "kind": "expense"}
                ]
            }
        },
    )


async def test_create_expense_without_api_key_returns_403(client: AsyncClient) -> None:
    response = await client.post("/expenses", json={"action": "spend", "amount_eur": 10})
    assert response.status_code == 403


async def test_create_expense_spend_records_punctual(client: AsyncClient, state: AppState) -> None:
    """Une dépense ponctuelle est persistée via add_punctual, date par défaut = aujourd'hui."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from bot.finance.models import Expense

    fake = Expense(
        kind="punctual",
        amount_cents=1250,
        label="courses",
        category="alimentation",
        occurred_on=date(2026, 6, 10),
        shared=False,
    )
    fake.id = 7
    state.deps.expenses.add_punctual = AsyncMock(return_value=fake)

    response = await client.post(
        "/expenses",
        headers={"X-API-Key": API_KEY},
        json={
            "action": "spend",
            "amount_eur": 12.5,
            "label": "courses",
            "category": "alimentation",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["recorded"] is True
    assert body["transaction"]["kind"] == "punctual"
    assert body["transaction"]["amount_eur"] == 12.5
    assert body["transaction"]["category"] == "alimentation"

    kwargs = state.deps.expenses.add_punctual.call_args.kwargs
    assert kwargs["amount_cents"] == 1250
    assert kwargs["shared"] is False
    today = datetime.now(ZoneInfo("Europe/Paris")).date()
    assert kwargs["occurred_on"] == today


async def test_create_expense_spend_shared_flag(client: AsyncClient, state: AppState) -> None:
    from bot.finance.models import Expense

    fake = Expense(
        kind="punctual",
        amount_cents=2000,
        label="resto",
        category=None,
        occurred_on=date(2026, 6, 9),
        shared=True,
    )
    fake.id = 8
    state.deps.expenses.add_punctual = AsyncMock(return_value=fake)

    response = await client.post(
        "/expenses",
        headers={"X-API-Key": API_KEY},
        json={"action": "spend", "amount_eur": 20, "label": "resto", "shared": True},
    )
    assert response.status_code == 200
    assert state.deps.expenses.add_punctual.call_args.kwargs["shared"] is True
    assert response.json()["transaction"]["shared"] is True


async def test_create_expense_income_starts_cycle(client: AsyncClient, state: AppState) -> None:
    """Un revenu avec starts_cycle ancre un cycle puis enregistre la ligne income."""
    from bot.finance.models import Expense

    fake = Expense(
        kind="income",
        amount_cents=250000,
        label="Salaire",
        category=None,
        occurred_on=date(2026, 6, 1),
        shared=False,
    )
    fake.id = 9
    state.deps.expenses.add_income = AsyncMock(return_value=fake)
    state.deps.expenses.start_cycle = AsyncMock()

    response = await client.post(
        "/expenses",
        headers={"X-API-Key": API_KEY},
        json={
            "action": "income",
            "amount_eur": 2500,
            "label": "Salaire",
            "occurred_on": "2026-06-01",
            "starts_cycle": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["transaction"]["kind"] == "income"
    state.deps.expenses.start_cycle.assert_awaited_once_with(date(2026, 6, 1))
    assert state.deps.expenses.add_income.call_args.kwargs["amount_cents"] == 250000


async def test_create_expense_tick_recurring_records_then_idempotent(
    client: AsyncClient, state: AppState
) -> None:
    """Premier tick enregistré (recorded=True), second ignoré (recorded=False)."""
    from bot.finance.models import Expense

    state.deps.profile = _profile_with_recurring()
    tick = Expense(
        kind="recurring_tick",
        amount_cents=80000,
        label="Loyer",
        recurring_key="loyer",
        category=None,
        occurred_on=date(2026, 6, 5),
        shared=False,
    )
    tick.id = 10
    # Premier appel → tick ; second → None (déjà pointé dans le cycle).
    state.deps.expenses.tick_recurring_once = AsyncMock(side_effect=[tick, None])

    first = await client.post(
        "/expenses",
        headers={"X-API-Key": API_KEY},
        json={"action": "tick_recurring", "recurring_key": "loyer"},
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["recorded"] is True
    assert first_body["transaction"]["recurring_key"] == "loyer"
    # Montant repris du YAML (800€) faute d'override.
    assert state.deps.expenses.tick_recurring_once.call_args.kwargs["amount_cents"] == 80000

    second = await client.post(
        "/expenses",
        headers={"X-API-Key": API_KEY},
        json={"action": "tick_recurring", "recurring_key": "loyer"},
    )
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["recorded"] is False
    assert second_body["transaction"] is None


async def test_create_expense_tick_unknown_key_returns_404(
    client: AsyncClient, state: AppState
) -> None:
    state.deps.profile = _profile_with_recurring()
    response = await client.post(
        "/expenses",
        headers={"X-API-Key": API_KEY},
        json={"action": "tick_recurring", "recurring_key": "inconnu"},
    )
    assert response.status_code == 404


async def test_create_expense_spend_without_amount_returns_400(client: AsyncClient) -> None:
    response = await client.post(
        "/expenses",
        headers={"X-API-Key": API_KEY},
        json={"action": "spend", "label": "courses"},
    )
    assert response.status_code == 400


async def test_create_expense_negative_amount_returns_400(client: AsyncClient) -> None:
    response = await client.post(
        "/expenses",
        headers={"X-API-Key": API_KEY},
        json={"action": "income", "amount_eur": -5},
    )
    assert response.status_code == 400


async def test_create_expense_bad_date_returns_400(client: AsyncClient) -> None:
    response = await client.post(
        "/expenses",
        headers={"X-API-Key": API_KEY},
        json={"action": "spend", "amount_eur": 10, "occurred_on": "10/06/2026"},
    )
    assert response.status_code == 400


# --- Cache statique (revalidation des modules ES) --------------------------


async def test_static_assets_sent_with_no_cache(client: AsyncClient) -> None:
    """Les modules ES internes doivent revalider (sinon import cassé après deploy).

    `index.html` ne versionne que `main.js?v=N` ; les imports internes sont des
    chemins nus. Sans revalidation, Safari peut servir un module périmé et
    casser tout le graphe ES. On verrouille donc `Cache-Control: no-cache`.
    """
    response = await client.get("/static/js/main.js")
    assert response.status_code == 200
    assert "no-cache" in response.headers.get("cache-control", "")
