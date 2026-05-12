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
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from bot.api import AppState, create_app
from bot.db import create_shared_engine
from bot.notifications.store import NotificationStore
from bot.pipeline import BotDeps
from bot.profile import UserProfile

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
    "search_query": None,
}


def _build_deps() -> BotDeps:
    settings = MagicMock()
    settings.api_key = API_KEY
    settings.timezone = "Europe/Paris"
    settings.home_lat = 48.26
    settings.home_lon = 7.45
    settings.home_city = "Sélestat"
    settings.fuel_default_radius_km = 10.0

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

    return BotDeps(
        settings=settings,
        llm=llm,
        memory=memory,
        tasks=MagicMock(),
        scheduler=MagicMock(),
        search=MagicMock(),
        rss=MagicMock(),
        rss_fetcher=MagicMock(),
        briefing=MagicMock(),
        calendar=MagicMock(),
        fuel=MagicMock(),
        geocoder=MagicMock(),
        weather=MagicMock(),
        profile=UserProfile(raw_yaml="", is_loaded=False),
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
    deps = _build_deps()
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
    from bot.briefing.weather import WeatherError

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
    from bot.briefing.weather import WeatherError

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
    assert body["briefing"] is None


async def test_dashboard_populates_weather_when_available(
    client: AsyncClient, state: AppState
) -> None:
    from bot.briefing.weather import WeatherSummary

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
