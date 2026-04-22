"""Tests d'intégration du ProactivityService avec les 5 garde-fous.

Engine SQLite réel sur `tmp_path`, Open-Meteo + iCloud mockés, `send` est un
`AsyncMock` pour vérifier les envois sans toucher à Telegram.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from bot.briefing.weather import HourlyPrecipitation
from bot.calendar.models import CalendarEvent
from bot.db import create_shared_engine
from bot.proactivity.models import NotificationLog
from bot.proactivity.service import ProactivityService
from bot.tasks.manager import TaskManager

TZ = ZoneInfo("Europe/Paris")


def _settings(
    *,
    enabled: bool = True,
    window: tuple[int, int] = (0, 24),
    budget: int = 3,
    rain_cooldown_h: int = 3,
) -> MagicMock:
    s = MagicMock()
    s.proactivity_enabled = enabled
    s.proactivity_window_start_hour = window[0]
    s.proactivity_window_end_hour = window[1]
    s.proactivity_daily_budget = budget
    s.proactivity_check_interval_min = 30
    s.proactivity_rain_cooldown_hours = rain_cooldown_h
    s.timezone = "Europe/Paris"
    s.home_lat = 48.26
    s.home_lon = 7.45
    return s


def _upcoming_event(in_min: int = 60, uid: str = "abc") -> CalendarEvent:
    now = datetime.now(TZ)
    start = now + timedelta(minutes=in_min)
    return CalendarEvent(
        uid=uid,
        title="Réunion équipe",
        start=start,
        end=start + timedelta(hours=1),
        location=None,
        description=None,
        calendar_name="Personnel",
    )


def _rainy_hour(mm: float = 1.0, proba: int = 90) -> HourlyPrecipitation:
    return HourlyPrecipitation(time=datetime.now(TZ), mm=mm, probability_pct=proba)


@pytest.fixture
async def engine(tmp_data_dir: Path) -> AsyncEngine:
    eng = create_shared_engine(tmp_data_dir / "tasks.db")
    await TaskManager(eng).init_schema()
    yield eng
    await eng.dispose()


def _weather_returning(hourly: list[HourlyPrecipitation]) -> MagicMock:
    w = MagicMock()
    w.get_hourly_precipitation = AsyncMock(return_value=hourly)
    return w


def _calendar_with(events: list[CalendarEvent], connected: bool = True) -> MagicMock:
    cal = MagicMock()
    cal.is_connected = connected
    cal.list_all_between = AsyncMock(return_value=events)
    return cal


async def _build_service(
    engine: AsyncEngine,
    settings: MagicMock,
    *,
    weather: MagicMock | None = None,
    calendar: MagicMock | None = None,
    send: AsyncMock | None = None,
) -> tuple[ProactivityService, AsyncMock]:
    send = send or AsyncMock()
    return (
        ProactivityService(
            settings=settings,
            weather=weather or _weather_returning([]),
            calendar=calendar or _calendar_with([]),
            engine=engine,
            chat_id=42,
            send=send,
        ),
        send,
    )


async def _count_logs(engine: AsyncEngine) -> int:
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        from sqlalchemy import select
        from sqlalchemy.sql import functions as fn

        result = await session.execute(select(fn.count()).select_from(NotificationLog))
        return int(result.scalar_one())


# ---------- Garde-fous ----------


async def test_disabled_does_nothing(engine: AsyncEngine) -> None:
    service, send = await _build_service(
        engine,
        _settings(enabled=False),
        calendar=_calendar_with([_upcoming_event()]),
    )
    await service.tick()
    send.assert_not_called()


async def test_outside_window_does_nothing(engine: AsyncEngine) -> None:
    # Fenêtre [0,0) = impossible (always out) : plus simple pour tester sans patcher now.
    service, send = await _build_service(
        engine,
        _settings(window=(0, 0)),
        calendar=_calendar_with([_upcoming_event()]),
    )
    await service.tick()
    send.assert_not_called()


async def test_budget_reached_does_nothing(engine: AsyncEngine) -> None:
    # Pré-remplir 3 logs envoyés aujourd'hui (UTC).
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        for _ in range(3):
            session.add(NotificationLog(kind="rain", sent_at=datetime.now(UTC)))
        await session.commit()

    service, send = await _build_service(
        engine,
        _settings(budget=3),
        calendar=_calendar_with([_upcoming_event()]),
    )
    await service.tick()
    send.assert_not_called()


# ---------- Règles ----------


async def test_event_in_window_triggers_and_logs(engine: AsyncEngine) -> None:
    service, send = await _build_service(
        engine,
        _settings(),
        calendar=_calendar_with([_upcoming_event(in_min=60, uid="e1")]),
    )
    await service.tick()

    send.assert_awaited_once()
    assert "Réunion équipe" in send.call_args.args[1]
    assert await _count_logs(engine) == 1


async def test_same_event_not_notified_twice(engine: AsyncEngine) -> None:
    service, send = await _build_service(
        engine,
        _settings(),
        calendar=_calendar_with([_upcoming_event(in_min=60, uid="e1")]),
    )
    await service.tick()
    await service.tick()  # 2e tick avec le même event

    send.assert_awaited_once()
    assert await _count_logs(engine) == 1


async def test_rain_triggers_when_no_event(engine: AsyncEngine) -> None:
    service, send = await _build_service(
        engine,
        _settings(),
        weather=_weather_returning([_rainy_hour(mm=1.0, proba=90)]),
        calendar=_calendar_with([]),
    )
    await service.tick()

    send.assert_awaited_once()
    assert "Parapluie" in send.call_args.args[1]


async def test_rain_cooldown_blocks_second_call(engine: AsyncEngine) -> None:
    # Log pluie récent (il y a 10 min) → cooldown 3 h doit bloquer.
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        recent = datetime.now(UTC) - timedelta(minutes=10)
        session.add(NotificationLog(kind="rain", sent_at=recent))
        await session.commit()

    service, send = await _build_service(
        engine,
        _settings(rain_cooldown_h=3),
        weather=_weather_returning([_rainy_hour(mm=2.0, proba=95)]),
        calendar=_calendar_with([]),
    )
    await service.tick()
    send.assert_not_called()


async def test_event_wins_over_rain(engine: AsyncEngine) -> None:
    service, send = await _build_service(
        engine,
        _settings(),
        weather=_weather_returning([_rainy_hour(mm=2.0, proba=95)]),
        calendar=_calendar_with([_upcoming_event(in_min=60, uid="important")]),
    )
    await service.tick()

    send.assert_awaited_once()
    text = send.call_args.args[1]
    assert "Réunion" in text and "Parapluie" not in text


async def test_exceptions_are_swallowed(engine: AsyncEngine) -> None:
    """Une panne iCloud ne doit jamais faire crasher le job APScheduler."""
    bad_calendar = MagicMock()
    bad_calendar.is_connected = True
    bad_calendar.list_all_between = AsyncMock(side_effect=RuntimeError("iCloud down"))

    service, send = await _build_service(
        engine,
        _settings(),
        calendar=bad_calendar,
        weather=_weather_returning([]),
    )
    # Ne doit pas lever.
    await service.tick()
    send.assert_not_called()


async def test_disconnected_calendar_skips_events_but_allows_rain(engine: AsyncEngine) -> None:
    cal = MagicMock()
    cal.is_connected = False
    cal.list_all_between = AsyncMock(side_effect=AssertionError("should not be called"))

    service, send = await _build_service(
        engine,
        _settings(),
        calendar=cal,
        weather=_weather_returning([_rainy_hour(mm=2.0, proba=95)]),
    )
    await service.tick()

    send.assert_awaited_once()
    assert "Parapluie" in send.call_args.args[1]
