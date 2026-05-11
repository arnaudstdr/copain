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

API_KEY = "test-secret"


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
    assert body == {"response": "Bonjour Arnaud."}


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
