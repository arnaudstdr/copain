"""Tests bout en bout de `POST /ask/stream` (SSE).

Couvre la couche HTTP au-dessus de `process_message_stream` :
- l'authentification (403 sans clé)
- le format des frames SSE (`data: {json}\\n\\n`)
- l'ordre delta → done et le filtrage du bloc <meta> (même coupé en deux)
- le fallback `replace` + `done` quand le bloc <meta> est invalide
- les frames `error` (LLMTimeoutError, LLMError, exception générique)
- les side effects (task) déclenchés via le chemin streamé + refresh_cards
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from bot.api import AppState, create_app
from bot.llm.client import LLMError, LLMTimeoutError
from bot.notifications.store import NotificationStore
from bot.pipeline import FALLBACK_TEXT

from .test_api import API_KEY, _build_deps

_ANSWER_META = (
    '<meta>{"intent":"answer","store_memory":false,"memory_content":null,'
    '"task":{"content":null,"due_str":null},'
    '"feed":{"action":null,"name":null,"url":null},'
    '"event":{"action":null,"title":null,"start_str":null,"end_str":null,'
    '"location":null,"description":null,"range_str":null,"calendar_name":null},'
    '"fuel":{"fuel_type":null,"radius_km":null,"location":null},'
    '"weather":{"location":null,"when":null},"search_query":null}</meta>'
)

_TASK_META = (
    '<meta>{"intent":"task","store_memory":false,"memory_content":null,'
    '"task":{"content":"acheter du pain","due_str":null},'
    '"feed":{"action":null,"name":null,"url":null},'
    '"event":{"action":null,"title":null,"start_str":null,"end_str":null,'
    '"location":null,"description":null,"range_str":null,"calendar_name":null},'
    '"fuel":{"fuel_type":null,"radius_km":null,"location":null},'
    '"weather":{"location":null,"when":null},"search_query":null}</meta>'
)


def _chunk_stream(chunks: list[str]) -> Any:
    """Construit un faux `chat_stream` qui yield les chunks donnés."""

    def factory(*args: Any, **kwargs: Any) -> AsyncIterator[str]:
        async def gen() -> AsyncIterator[str]:
            for chunk in chunks:
                yield chunk

        return gen()

    return factory


def _failing_stream(exc: Exception, after: list[str] | None = None) -> Any:
    """Faux `chat_stream` qui lève `exc` (après d'éventuels chunks)."""

    def factory(*args: Any, **kwargs: Any) -> AsyncIterator[str]:
        async def gen() -> AsyncIterator[str]:
            for chunk in after or []:
                yield chunk
            raise exc

        return gen()

    return factory


def _parse_sse(body: str) -> list[dict[str, Any]]:
    """Parse un corps SSE complet en liste de payloads JSON."""
    frames: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        assert block.startswith("data: "), f"frame SSE mal formée : {block!r}"
        frames.append(json.loads(block[len("data: ") :]))
    return frames


@pytest.fixture
async def state(tmp_data_dir: Any) -> AppState:
    from pathlib import Path

    from bot.db import create_shared_engine

    engine = create_shared_engine(Path(tmp_data_dir) / "tasks.db")
    notifications = NotificationStore(engine)
    await notifications.init_schema()
    deps = _build_deps()
    state = AppState(settings=deps.settings, deps=deps, notifications=notifications)
    yield state
    await engine.dispose()


@pytest.fixture
async def client(state: AppState) -> AsyncIterator[AsyncClient]:
    app = create_app(state)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# --- auth ---------------------------------------------------------------


async def test_stream_without_api_key_returns_403(client: AsyncClient) -> None:
    response = await client.post("/ask/stream", json={"message": "salut"})
    assert response.status_code == 403


# --- happy path ---------------------------------------------------------


async def test_stream_answer_emits_deltas_then_done(client: AsyncClient, state: AppState) -> None:
    state.deps.llm.chat_stream = _chunk_stream(["Bonjour ", "Arnaud.\n", _ANSWER_META])
    response = await client.post(
        "/ask/stream",
        headers={"X-API-Key": API_KEY},
        json={"message": "salut"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    frames = _parse_sse(response.text)
    assert frames[-1]["type"] == "done"
    assert frames[-1]["intent"] == "answer"
    assert frames[-1]["refresh_cards"] == []
    deltas = [f["text"] for f in frames if f["type"] == "delta"]
    assert "".join(deltas) == "Bonjour Arnaud."


async def test_stream_never_leaks_meta_block_split_across_chunks(
    client: AsyncClient, state: AppState
) -> None:
    """Le bloc <meta> coupé entre deux chunks Ollama ne doit jamais fuir."""
    # Le marqueur "<meta>" lui-même est coupé en plein milieu ("<me" | "ta>…").
    state.deps.llm.chat_stream = _chunk_stream(
        ["Réponse.", "\n" + _ANSWER_META[:3], _ANSWER_META[3:]]
    )
    response = await client.post(
        "/ask/stream",
        headers={"X-API-Key": API_KEY},
        json={"message": "salut"},
    )
    visible = "".join(f.get("text", "") for f in _parse_sse(response.text))
    assert "<meta" not in visible
    assert "intent" not in visible


# --- meta invalide --------------------------------------------------------


async def test_stream_invalid_meta_falls_back_with_replace_and_done(
    client: AsyncClient, state: AppState
) -> None:
    state.deps.llm.chat_stream = _chunk_stream(["Texte.", "<meta>{json cassé</meta>"])
    response = await client.post(
        "/ask/stream",
        headers={"X-API-Key": API_KEY},
        json={"message": "salut"},
    )
    frames = _parse_sse(response.text)
    types = [f["type"] for f in frames]
    assert "replace" in types
    replace = next(f for f in frames if f["type"] == "replace")
    assert replace["text"] == FALLBACK_TEXT
    assert frames[-1]["type"] == "done"
    assert frames[-1]["intent"] == "answer"


# --- erreurs --------------------------------------------------------------


async def test_stream_llm_timeout_emits_error_frame(client: AsyncClient, state: AppState) -> None:
    state.deps.llm.chat_stream = _failing_stream(LLMTimeoutError("timeout"))
    response = await client.post(
        "/ask/stream",
        headers={"X-API-Key": API_KEY},
        json={"message": "salut"},
    )
    # Le status est figé à 200 dès l'ouverture du stream : l'erreur passe en frame.
    assert response.status_code == 200
    frames = _parse_sse(response.text)
    assert frames[-1]["type"] == "error"
    assert "longtemps" in frames[-1]["text"]


async def test_stream_llm_error_emits_error_frame(client: AsyncClient, state: AppState) -> None:
    state.deps.llm.chat_stream = _failing_stream(LLMError("boom"))
    response = await client.post(
        "/ask/stream",
        headers={"X-API-Key": API_KEY},
        json={"message": "salut"},
    )
    frames = _parse_sse(response.text)
    assert frames[-1]["type"] == "error"


async def test_stream_unexpected_error_midstream_emits_error_frame(
    client: AsyncClient, state: AppState
) -> None:
    """Une exception après les premiers deltas doit clore par une frame error."""
    state.deps.llm.chat_stream = _failing_stream(RuntimeError("crash"), after=["Début de répon"])
    response = await client.post(
        "/ask/stream",
        headers={"X-API-Key": API_KEY},
        json={"message": "salut"},
    )
    frames = _parse_sse(response.text)
    assert frames[-1]["type"] == "error"
    assert "interne" in frames[-1]["text"]


# --- side effects ----------------------------------------------------------


async def test_stream_task_intent_applies_side_effects_and_refresh_cards(
    client: AsyncClient, state: AppState
) -> None:
    fake_task = MagicMock()
    fake_task.id = 7
    fake_task.content = "acheter du pain"
    state.deps.tasks.create = AsyncMock(return_value=fake_task)
    state.deps.llm.chat_stream = _chunk_stream(["Noté.\n", _TASK_META])
    response = await client.post(
        "/ask/stream",
        headers={"X-API-Key": API_KEY},
        json={"message": "ajoute acheter du pain"},
    )
    frames = _parse_sse(response.text)
    assert frames[-1]["type"] == "done"
    assert frames[-1]["intent"] == "task"
    assert "today_tasks" in frames[-1]["refresh_cards"]
    state.deps.tasks.create.assert_awaited_once()
