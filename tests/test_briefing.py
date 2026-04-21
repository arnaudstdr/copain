"""Tests du BriefingService avec dépendances mockées."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.briefing.service import BriefingService
from bot.briefing.weather import WeatherError, WeatherSummary
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
async def real_tasks(tmp_data_dir: Path) -> TaskManager:
    mgr = TaskManager(tmp_data_dir / "tasks.db")
    await mgr.init_schema()
    yield mgr
    await mgr.dispose()


async def test_build_contains_three_sections(
    fake_settings: MagicMock,
    mock_weather: MagicMock,
    mock_rss: MagicMock,
    mock_rss_fetcher: MagicMock,
    mock_llm: MagicMock,
    real_tasks: TaskManager,
) -> None:
    service = BriefingService(
        settings=fake_settings,
        weather=mock_weather,
        tasks=real_tasks,
        rss=mock_rss,
        rss_fetcher=mock_rss_fetcher,
        llm=mock_llm,
    )
    text = await service.build()
    assert "Sélestat" in text
    assert "14°C" in text or "14.5" in text or "15°C" in text
    assert "Tâches du jour" in text
    assert "Actus du jour" in text


async def test_build_with_today_task(
    fake_settings: MagicMock,
    mock_weather: MagicMock,
    mock_rss: MagicMock,
    mock_rss_fetcher: MagicMock,
    mock_llm: MagicMock,
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
    )
    text = await service.build()
    assert "acheter du pain" in text


async def test_build_weather_error_is_graceful(
    fake_settings: MagicMock,
    mock_rss: MagicMock,
    mock_rss_fetcher: MagicMock,
    mock_llm: MagicMock,
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
    )
    text = await service.build()
    assert "Météo indisponible" in text
    assert "Tâches du jour" in text


async def test_build_no_feeds_skips_rss(
    fake_settings: MagicMock,
    mock_weather: MagicMock,
    mock_llm: MagicMock,
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
    )
    text = await service.build()
    assert "Actus du jour" not in text
    mock_llm.chat.assert_not_called()
