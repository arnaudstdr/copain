"""Fixtures partagées pour la suite de tests."""

from __future__ import annotations

import copy
from collections import deque
from collections.abc import AsyncIterator, Iterator
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

if TYPE_CHECKING:
    from bot.llm.parser import Meta
    from bot.pipeline.core import BotDeps

SAMPLE_META_JSON = """\
<meta>
{
  "intent": "task",
  "store_memory": true,
  "memory_content": "Arnaud veut arroser les plantes demain.",
  "task": {
    "content": "arroser les plantes",
    "due_str": "demain 18h"
  },
  "search_query": null
}
</meta>
"""

SAMPLE_LLM_RESPONSE = f"D'accord, je te le rappelle demain à 18h.\n{SAMPLE_META_JSON}"


@pytest.fixture
def sample_llm_response() -> str:
    return SAMPLE_LLM_RESPONSE


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Iterator[Path]:
    data = tmp_path / "data"
    data.mkdir()
    yield data


@pytest.fixture
def mock_embedder() -> AsyncMock:
    """Embedder mocké qui renvoie un vecteur déterministe de petite taille."""
    embedder = AsyncMock()
    embedder.embed.return_value = [0.1] * 8
    return embedder


@pytest.fixture
async def chroma_persist_dir(tmp_data_dir: Path) -> AsyncIterator[Path]:
    chroma = tmp_data_dir / "chroma"
    chroma.mkdir()
    yield chroma


# --- Fabrique de Meta neutre -------------------------------------------------

# Meta « tout vide » (intent answer) : déclenche aucun side effect ni handler.
# Sert de base à `make_meta` ; structure alignée sur `Meta` (bot/llm/parser.py)
# et sur le `_FALLBACK_META` de bot/pipeline/core.py.
_NEUTRAL_META: dict[str, Any] = {
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


def make_meta(**overrides: Any) -> Meta:
    """Construit une `Meta` valide à partir du neutre, surchargée par `overrides`.

    Les sous-dicts (`task`, `feed`, `event`, …) sont fusionnés clé à clé : on
    peut passer `weather={"location": "Paris"}` sans réécrire `when`. Les
    clés scalaires (`intent`, `store_memory`, …) remplacent directement.
    """
    meta = copy.deepcopy(_NEUTRAL_META)
    for key, value in overrides.items():
        base = meta.get(key)
        if isinstance(base, dict) and isinstance(value, dict):
            base.update(value)
        else:
            meta[key] = value
    return meta  # type: ignore[return-value]


@pytest.fixture
def make_meta_factory() -> Any:
    """Expose `make_meta` comme fixture pour les tests qui préfèrent l'injecter."""
    return make_meta


# --- Settings mocké partagé --------------------------------------------------


def make_settings(**overrides: Any) -> MagicMock:
    """`Settings` mocké aux valeurs de référence des tests (domicile à Sélestat,
    bureau à Obernai, fuseau Europe/Paris).

    Centralise le bloc dupliqué dans les fixtures `deps` de la suite. Les
    `overrides` permettent à un test de surcharger un champ précis (ex.
    `make_settings(api_key=API_KEY)` pour `test_api`).
    """
    settings = MagicMock()
    settings.api_key = "test-api-key"
    settings.timezone = "Europe/Paris"
    settings.home_lat = 48.26
    settings.home_lon = 7.45
    settings.home_city = "Sélestat"
    settings.work_lat = 48.46
    settings.work_lon = 7.48
    settings.work_city = "Obernai"
    settings.fuel_default_radius_km = 10.0
    settings.foryou_similarity_max_distance = 0.35
    settings.max_history = 6
    settings.chat_history_retention_days = 30
    settings.open_worries_prompt_limit = 10
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


# --- BotDeps mocké pour les handlers / side effects --------------------------


def build_mock_deps() -> BotDeps:
    """`BotDeps` entièrement mocké, réglé pour les tests de handlers/side effects.

    Chaque sous-système est un `MagicMock` ; les coroutines exposées sont des
    `AsyncMock` avec un retour neutre (vide / succès). Les tests surchargent
    ensuite ce dont ils ont besoin (`deps.fuel.find_cheapest.side_effect = …`).
    """
    from bot.pipeline.core import BotDeps

    settings = make_settings()

    memory = MagicMock()
    memory.store = AsyncMock()
    memory.store_depot = AsyncMock()
    memory.find_similar_depots = AsyncMock(return_value=[])

    llm = MagicMock()
    llm.chat = AsyncMock(return_value="Résumé des articles.")

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
    expenses.tick_recurring_once = AsyncMock(return_value=fake_expense)
    expenses.start_cycle = AsyncMock(return_value=fake_expense)
    expenses.list_for_cycle = AsyncMock(return_value=[])
    expenses.current_cycle_bounds = AsyncMock(return_value=(date(2026, 6, 1), date(2026, 7, 1)))

    scheduler = MagicMock()
    scheduler.add_reminder = MagicMock()

    rss = MagicMock()
    rss.add = AsyncMock()
    rss.list = AsyncMock(return_value=[])
    rss.get = AsyncMock(return_value=None)
    rss.remove = AsyncMock(return_value=True)
    rss_fetcher = MagicMock()
    rss_fetcher.fetch_many = AsyncMock(return_value=[])

    calendar = MagicMock()
    calendar.is_connected = True
    calendar.list_all_between = AsyncMock(return_value=[])
    calendar.create_event = AsyncMock()

    fuel = MagicMock()
    fuel.find_cheapest = AsyncMock(return_value=[])

    geocoder = MagicMock()
    geocoder.geocode_fr = AsyncMock(return_value=None)

    weather = MagicMock()
    weather.get_forecast = AsyncMock(return_value=[])

    return BotDeps(
        settings=settings,
        llm=llm,
        memory=memory,
        tasks=tasks,
        thoughts=thoughts,
        expenses=expenses,
        scheduler=scheduler,
        search=MagicMock(),
        rss=rss,
        rss_fetcher=rss_fetcher,
        calendar=calendar,
        fuel=fuel,
        geocoder=geocoder,
        weather=weather,
        news=MagicMock(),
        foryou=MagicMock(),
        profile=MagicMock(),
        location_events=MagicMock(),
        proactivity=MagicMock(),
        history=deque(maxlen=settings.max_history),
    )


@pytest.fixture
def bot_deps() -> BotDeps:
    return build_mock_deps()
