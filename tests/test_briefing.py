"""Tests du BriefingService avec dépendances mockées."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.briefing.service import BriefingService
from bot.briefing.weather import WeatherError, WeatherSummary
from bot.calendar.models import CalendarEvent
from bot.rss.fetcher import FeedItem
from bot.tasks.manager import TaskManager


@pytest.fixture
def fake_settings() -> MagicMock:
    s = MagicMock()
    s.home_lat = 48.26
    s.home_lon = 7.45
    s.home_city = "Sélestat"
    s.timezone = "Europe/Paris"
    s.telegram_bot_token = "fake-token"
    s.allowed_user_id = 42
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


@pytest.fixture
def mock_rss() -> MagicMock:
    m = MagicMock()
    m.list = AsyncMock(return_value=[MagicMock(name="Feed1")])
    return m


@pytest.fixture
def mock_rss_fetcher() -> MagicMock:
    f = MagicMock()
    f.fetch_many = AsyncMock(
        return_value=[
            FeedItem(
                feed_name="The Verge",
                title="Un gros titre",
                url="https://example.com/1",
                summary="Contenu intéressant sur la tech.",
                published=datetime.now(UTC),
            )
        ]
    )
    return f


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.chat = AsyncMock(return_value="- [The Verge] Résumé (https://example.com/1)")
    return llm


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
    mock_rss: MagicMock,
    mock_rss_fetcher: MagicMock,
    mock_llm: MagicMock,
    mock_calendar_empty: MagicMock,
    real_tasks: TaskManager,
) -> None:
    service = BriefingService(
        settings=fake_settings,
        weather=mock_weather,
        tasks=real_tasks,
        rss=mock_rss,
        rss_fetcher=mock_rss_fetcher,
        llm=mock_llm,
        calendar=mock_calendar_empty,
    )
    text = await service.build()
    assert "Sélestat" in text
    assert "Tâches du jour" in text
    assert "Évènements du jour" in text
    assert "Actus du jour" in text


async def test_build_with_today_task(
    fake_settings: MagicMock,
    mock_weather: MagicMock,
    mock_rss: MagicMock,
    mock_rss_fetcher: MagicMock,
    mock_llm: MagicMock,
    mock_calendar_empty: MagicMock,
    real_tasks: TaskManager,
) -> None:
    now = datetime.now(UTC) + timedelta(hours=2)
    await real_tasks.create("acheter du pain", due_at=now)

    service = BriefingService(
        settings=fake_settings,
        weather=mock_weather,
        tasks=real_tasks,
        rss=mock_rss,
        rss_fetcher=mock_rss_fetcher,
        llm=mock_llm,
        calendar=mock_calendar_empty,
    )
    text = await service.build()
    assert "acheter du pain" in text


async def test_build_with_events(
    fake_settings: MagicMock,
    mock_weather: MagicMock,
    mock_rss: MagicMock,
    mock_rss_fetcher: MagicMock,
    mock_llm: MagicMock,
    mock_calendar_with_events: MagicMock,
    real_tasks: TaskManager,
) -> None:
    service = BriefingService(
        settings=fake_settings,
        weather=mock_weather,
        tasks=real_tasks,
        rss=mock_rss,
        rss_fetcher=mock_rss_fetcher,
        llm=mock_llm,
        calendar=mock_calendar_with_events,
    )
    text = await service.build()
    assert "Standup équipe" in text
    assert "RDV dentiste" in text
    assert "Bureau" in text


async def test_build_calendar_disconnected_shows_empty_section(
    fake_settings: MagicMock,
    mock_weather: MagicMock,
    mock_rss: MagicMock,
    mock_rss_fetcher: MagicMock,
    mock_llm: MagicMock,
    mock_calendar_disconnected: MagicMock,
    real_tasks: TaskManager,
) -> None:
    service = BriefingService(
        settings=fake_settings,
        weather=mock_weather,
        tasks=real_tasks,
        rss=mock_rss,
        rss_fetcher=mock_rss_fetcher,
        llm=mock_llm,
        calendar=mock_calendar_disconnected,
    )
    text = await service.build()
    assert "Aucun évènement prévu" in text
    mock_calendar_disconnected.list_all_today.assert_not_called()


async def test_build_weather_error_is_graceful(
    fake_settings: MagicMock,
    mock_rss: MagicMock,
    mock_rss_fetcher: MagicMock,
    mock_llm: MagicMock,
    mock_calendar_empty: MagicMock,
    real_tasks: TaskManager,
) -> None:
    weather = MagicMock()
    weather.get_today = AsyncMock(side_effect=WeatherError("API down"))

    service = BriefingService(
        settings=fake_settings,
        weather=weather,
        tasks=real_tasks,
        rss=mock_rss,
        rss_fetcher=mock_rss_fetcher,
        llm=mock_llm,
        calendar=mock_calendar_empty,
    )
    text = await service.build()
    assert "Météo indisponible" in text
    assert "Tâches du jour" in text


async def test_build_no_feeds_skips_rss(
    fake_settings: MagicMock,
    mock_weather: MagicMock,
    mock_llm: MagicMock,
    mock_calendar_empty: MagicMock,
    real_tasks: TaskManager,
) -> None:
    rss = MagicMock()
    rss.list = AsyncMock(return_value=[])
    fetcher = MagicMock()
    fetcher.fetch_many = AsyncMock(return_value=[])

    service = BriefingService(
        settings=fake_settings,
        weather=mock_weather,
        tasks=real_tasks,
        rss=rss,
        rss_fetcher=fetcher,
        llm=mock_llm,
        calendar=mock_calendar_empty,
    )
    text = await service.build()
    assert "Actus du jour" not in text
    mock_llm.chat.assert_not_called()
