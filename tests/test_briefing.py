"""Tests du BriefingService avec dépendances mockées."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.briefing.service import BriefingService
from bot.briefing.weather import WeatherError, WeatherSummary
from bot.calendar.models import CalendarEvent
from bot.profile import UserProfile
from bot.tasks.manager import TaskManager


@pytest.fixture
def fake_settings() -> MagicMock:
    s = MagicMock()
    s.home_lat = 48.26
    s.home_lon = 7.45
    s.home_city = "Sélestat"
    s.timezone = "Europe/Paris"
    return s


@pytest.fixture
def mock_weather() -> MagicMock:
    w = MagicMock()
    w.get_today = AsyncMock(
        return_value=WeatherSummary(
            city="Sélestat",
            temp_current=14.5,
            temp_min=11.0,
            temp_max=18.0,
            precipitation_mm=2.0,
            wind_kmh=12.0,
            description="partiellement nuageux",
        )
    )
    return w


def _profile_with_topics(topics: list[str], blocklist: list[str] | None = None) -> UserProfile:
    data: dict[str, Any] = {
        "news_topics": {
            "daily_briefing": topics,
            "filters": {"domains_blocklist": blocklist or []},
        }
    }
    return UserProfile(raw_yaml="", is_loaded=True, data=data)


@pytest.fixture
def profile_with_news_topics() -> UserProfile:
    return _profile_with_topics(["LLM agents", "OpenAI"])


@pytest.fixture
def profile_without_news_topics() -> UserProfile:
    return UserProfile(raw_yaml="", is_loaded=True, data={})


@pytest.fixture
def mock_news() -> MagicMock:
    """NewsCurator qui retourne un résumé non-vide par défaut."""
    n = MagicMock()
    n.fetch_top_news = AsyncMock(
        return_value="- **GPT-X annoncé** (OpenAI) — nouveau modèle multimodal.\n  https://example.com/1"
    )
    return n


@pytest.fixture
def mock_calendar_empty() -> MagicMock:
    cal = MagicMock()
    cal.is_connected = True
    cal.list_all_today = AsyncMock(return_value=[])
    return cal


@pytest.fixture
def mock_calendar_with_events() -> MagicMock:
    cal = MagicMock()
    cal.is_connected = True
    start = datetime.now(UTC).replace(hour=9, minute=0, second=0, microsecond=0)
    cal.list_all_today = AsyncMock(
        return_value=[
            CalendarEvent(
                uid="e1",
                title="Standup équipe",
                start=start,
                end=start + timedelta(hours=1),
                location="Bureau",
                description=None,
                calendar_name="Personnel",
            ),
            CalendarEvent(
                uid="e2",
                title="RDV dentiste",
                start=start.replace(hour=14, minute=30),
                end=start.replace(hour=15, minute=30),
                location=None,
                description=None,
                calendar_name="Personnel",
            ),
        ]
    )
    return cal


@pytest.fixture
def mock_calendar_disconnected() -> MagicMock:
    cal = MagicMock()
    cal.is_connected = False
    cal.list_all_today = AsyncMock(side_effect=RuntimeError("disconnected"))
    return cal


@pytest.fixture
async def real_tasks(tmp_data_dir: Path) -> TaskManager:
    from bot.db import create_shared_engine

    engine = create_shared_engine(tmp_data_dir / "tasks.db")
    mgr = TaskManager(engine)
    await mgr.init_schema()
    yield mgr
    await engine.dispose()


async def test_build_contains_four_sections(
    fake_settings: MagicMock,
    mock_weather: MagicMock,
    mock_news: MagicMock,
    profile_with_news_topics: UserProfile,
    mock_calendar_empty: MagicMock,
    real_tasks: TaskManager,
) -> None:
    service = BriefingService(
        settings=fake_settings,
        weather=mock_weather,
        tasks=real_tasks,
        news=mock_news,
        profile=profile_with_news_topics,
        calendar=mock_calendar_empty,
    )
    text = await service.build()
    assert "Sélestat" in text
    assert "Tâches du jour" in text
    assert "Évènements du jour" in text
    assert "Actus IA" in text
    assert "GPT-X" in text


async def test_build_with_today_task(
    fake_settings: MagicMock,
    mock_weather: MagicMock,
    mock_news: MagicMock,
    profile_with_news_topics: UserProfile,
    mock_calendar_empty: MagicMock,
    real_tasks: TaskManager,
) -> None:
    now = datetime.now(UTC) + timedelta(hours=2)
    await real_tasks.create("acheter du pain", due_at=now)

    service = BriefingService(
        settings=fake_settings,
        weather=mock_weather,
        tasks=real_tasks,
        news=mock_news,
        profile=profile_with_news_topics,
        calendar=mock_calendar_empty,
    )
    text = await service.build()
    assert "acheter du pain" in text


async def test_build_with_events(
    fake_settings: MagicMock,
    mock_weather: MagicMock,
    mock_news: MagicMock,
    profile_with_news_topics: UserProfile,
    mock_calendar_with_events: MagicMock,
    real_tasks: TaskManager,
) -> None:
    service = BriefingService(
        settings=fake_settings,
        weather=mock_weather,
        tasks=real_tasks,
        news=mock_news,
        profile=profile_with_news_topics,
        calendar=mock_calendar_with_events,
    )
    text = await service.build()
    assert "Standup équipe" in text
    assert "RDV dentiste" in text
    assert "Bureau" in text


async def test_build_calendar_disconnected_shows_empty_section(
    fake_settings: MagicMock,
    mock_weather: MagicMock,
    mock_news: MagicMock,
    profile_with_news_topics: UserProfile,
    mock_calendar_disconnected: MagicMock,
    real_tasks: TaskManager,
) -> None:
    service = BriefingService(
        settings=fake_settings,
        weather=mock_weather,
        tasks=real_tasks,
        news=mock_news,
        profile=profile_with_news_topics,
        calendar=mock_calendar_disconnected,
    )
    text = await service.build()
    assert "Aucun évènement prévu" in text
    mock_calendar_disconnected.list_all_today.assert_not_called()


async def test_build_weather_error_is_graceful(
    fake_settings: MagicMock,
    mock_news: MagicMock,
    profile_with_news_topics: UserProfile,
    mock_calendar_empty: MagicMock,
    real_tasks: TaskManager,
) -> None:
    weather = MagicMock()
    weather.get_today = AsyncMock(side_effect=WeatherError("API down"))

    service = BriefingService(
        settings=fake_settings,
        weather=weather,
        tasks=real_tasks,
        news=mock_news,
        profile=profile_with_news_topics,
        calendar=mock_calendar_empty,
    )
    text = await service.build()
    assert "Météo indisponible" in text
    assert "Tâches du jour" in text


async def test_build_news_skipped_when_no_topics(
    fake_settings: MagicMock,
    mock_weather: MagicMock,
    mock_news: MagicMock,
    profile_without_news_topics: UserProfile,
    mock_calendar_empty: MagicMock,
    real_tasks: TaskManager,
) -> None:
    """Sans `news_topics` dans le profil → pas de section news, pas d'appel NewsCurator."""
    service = BriefingService(
        settings=fake_settings,
        weather=mock_weather,
        tasks=real_tasks,
        news=mock_news,
        profile=profile_without_news_topics,
        calendar=mock_calendar_empty,
    )
    text = await service.build()
    assert "Actus IA" not in text
    mock_news.fetch_top_news.assert_not_called()


async def test_build_news_failure_is_graceful(
    fake_settings: MagicMock,
    mock_weather: MagicMock,
    profile_with_news_topics: UserProfile,
    mock_calendar_empty: MagicMock,
    real_tasks: TaskManager,
) -> None:
    """Si NewsCurator plante, le briefing s'envoie quand même sans la section news."""
    news = MagicMock()
    news.fetch_top_news = AsyncMock(side_effect=RuntimeError("SearXNG down"))

    service = BriefingService(
        settings=fake_settings,
        weather=mock_weather,
        tasks=real_tasks,
        news=news,
        profile=profile_with_news_topics,
        calendar=mock_calendar_empty,
    )
    text = await service.build()
    assert "Sélestat" in text
    assert "Actus IA" not in text


async def test_build_passes_topics_and_blocklist_to_curator(
    fake_settings: MagicMock,
    mock_weather: MagicMock,
    mock_news: MagicMock,
    mock_calendar_empty: MagicMock,
    real_tasks: TaskManager,
) -> None:
    """La config du profil doit arriver intacte côté NewsCurator."""
    profile = _profile_with_topics(
        ["LLM agents", "OpenAI"], blocklist=["reddit.com", "twitter.com"]
    )
    service = BriefingService(
        settings=fake_settings,
        weather=mock_weather,
        tasks=real_tasks,
        news=mock_news,
        profile=profile,
        calendar=mock_calendar_empty,
    )
    await service.build()
    mock_news.fetch_top_news.assert_awaited_once()
    kwargs = mock_news.fetch_top_news.await_args.kwargs
    assert kwargs["topics"] == ["LLM agents", "OpenAI"]
    assert kwargs["domains_blocklist"] == ["reddit.com", "twitter.com"]
