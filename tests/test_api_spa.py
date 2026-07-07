"""Tests du serving SPA React (`SPAStaticFiles`) — unique interface web.

Depuis le cutover (front vanilla `bot/static/` supprimé), le build React
(`frontend/dist`) est le seul serving de `/` :

- `frontend/dist` présent → `create_app` monte `SPAStaticFiles` en catch-all sur
  `/` (index.html no-store, assets hashés, icônes, fallback SPA).
- `frontend/dist` absent (dev sans build, CI, clone frais) → aucun mount, un
  warning `frontend_dist_missing` est loggé au boot et `/` renvoie 404 (en dev
  pur on passe par `vite dev`, pas par ce serving).

Les tests pilotent ce choix en patchant `bot.api.FRONTEND_DIST` (le build réel
étant gitignoré, sa présence sur le disque est non déterministe).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from bot.api import AppState, create_app
from tests.conftest import build_mock_deps

# Marqueur injecté dans l'index.html du faux build React pour vérifier sans
# ambiguïté que c'est bien le build (`frontend/dist`) qui est servi.
REACT_INDEX_MARKER = "<!-- react-dist-index -->"


@pytest.fixture
def dist_dir(tmp_path: Path) -> Iterator[Path]:
    """Faux build Vite minimal : index.html marqué + un asset hashé."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        f'<!doctype html>{REACT_INDEX_MARKER}<div id="root"></div>',
        encoding="utf-8",
    )
    (dist / "assets" / "app-abc123.js").write_text(
        "console.log('react');",
        encoding="utf-8",
    )
    yield dist


def _make_state() -> AppState:
    deps = build_mock_deps()
    return AppState(settings=deps.settings, deps=deps, notifications=MagicMock())


@pytest.fixture
async def spa_client(dist_dir: Path) -> AsyncIterator[AsyncClient]:
    """Client sur une app dont le build React (`dist_dir`) existe → React servi."""
    with patch("bot.api.FRONTEND_DIST", dist_dir):
        app = create_app(_make_state())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# --- Serving React (dist présent) -------------------------------------------


async def test_root_serves_react_index_no_store(spa_client: AsyncClient) -> None:
    response = await spa_client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert REACT_INDEX_MARKER in response.text
    assert response.headers["cache-control"] == "no-store, must-revalidate"


async def test_hashed_asset_is_served(spa_client: AsyncClient) -> None:
    response = await spa_client.get("/assets/app-abc123.js")
    assert response.status_code == 200
    assert "console.log('react');" in response.text


async def test_unknown_path_falls_back_to_index(spa_client: AsyncClient) -> None:
    # Routing SPA : un chemin inconnu sous / retombe sur index.html (200).
    response = await spa_client.get("/une/route/inexistante")
    assert response.status_code == 200
    assert REACT_INDEX_MARKER in response.text


async def test_api_json_route_keeps_priority(spa_client: AsyncClient) -> None:
    # Une route explicite (résolue avant le mount catch-all) renvoie son JSON,
    # pas l'index.html du fallback SPA.
    response = await spa_client.get("/config")
    assert response.status_code == 200
    assert "api_key" in response.json()
    assert REACT_INDEX_MARKER not in response.text


async def test_api_auth_route_keeps_priority(spa_client: AsyncClient) -> None:
    # Le catch-all renverrait 200 (index.html) ; la route explicite protégée
    # doit primer et répondre 403 sans clé.
    response = await spa_client.post("/ask", json={"message": "salut"})
    assert response.status_code == 403


# --- Garde-fou : build absent (dev pur / CI) --------------------------------


async def test_missing_dist_logs_warning_and_serves_404(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with patch("bot.api.FRONTEND_DIST", missing), patch("bot.api.log") as mock_log:
        app = create_app(_make_state())

    # Un warning est loggé au boot, pas de crash.
    warned = [call.args[0] for call in mock_log.warning.call_args_list if call.args]
    assert "frontend_dist_missing" in warned

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/")
    # Aucun serving statique monté : `/` n'est plus une route connue → 404.
    assert response.status_code == 404
