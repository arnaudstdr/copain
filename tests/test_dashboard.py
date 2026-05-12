"""Tests unitaires du module `bot.dashboard`.

Couvre la logique d'agrégation (today_tasks, build_dashboard) en isolation
des couches HTTP et SQLite. Les tests E2E de `GET /dashboard` vivent dans
`tests/test_api.py`.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, time, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from bot.briefing.weather import WeatherError, WeatherSummary
from bot.calendar.client import ICloudCalendarError
from bot.calendar.models import CalendarEvent
from bot.dashboard import BRIEFING_TEXT_PREFIX, build_dashboard, today_tasks
from bot.pipeline import BotDeps
from bot.profile import UserProfile
from bot.tasks.models import Task

TZ = ZoneInfo("Europe/Paris")


@pytest.fixture
def deps() -> BotDeps:
    settings = MagicMock()
    settings.timezone = "Europe/Paris"
    settings.home_lat = 48.26
    settings.home_lon = 7.45
    settings.home_city = "Sélestat"
    settings.work_lat = 48.46
    settings.work_lon = 7.48
    settings.work_city = "Obernai"

    location_events = MagicMock()
    location_events.get_current_location = AsyncMock(return_value=None)

    return BotDeps(
        settings=settings,
        llm=MagicMock(),
        memory=MagicMock(),
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
        location_events=location_events,
        proactivity=MagicMock(),
        history=deque(maxlen=6),
    )


@pytest.fixture
def notifications_stub() -> MagicMock:
    stub = MagicMock()
    stub.count_unread = AsyncMock(return_value=0)
    stub.latest_with_text_prefix = AsyncMock(return_value=None)
    return stub


# --- today_tasks ------------------------------------------------------------


def _make_task(content: str, due_at: datetime | None) -> Task:
    t = Task(content=content, due_at=due_at)
    return t


def test_today_tasks_keeps_only_today_due() -> None:
    now = datetime.now(TZ)
    today_due = now.replace(hour=18, minute=0, second=0, microsecond=0)
    tomorrow_due = today_due + timedelta(days=1)
    pending = [
        _make_task("aujourd'hui", today_due),
        _make_task("demain", tomorrow_due),
        _make_task("sans due", None),
    ]
    result = today_tasks(pending, TZ)
    assert [t.content for t in result] == ["aujourd'hui"]


def test_today_tasks_treats_naive_due_as_local_tz() -> None:
    """Une tâche stockée naïve (legacy) est interprétée dans la tz locale, pas en UTC."""
    today_naive = datetime.now(TZ).replace(hour=10, minute=0, second=0, microsecond=0, tzinfo=None)
    pending = [_make_task("naive", today_naive)]
    result = today_tasks(pending, TZ)
    assert len(result) == 1


def test_today_tasks_empty_returns_empty() -> None:
    assert today_tasks([], TZ) == []


# --- build_dashboard --------------------------------------------------------


async def test_build_dashboard_aggregates_all_sources(
    deps: BotDeps, notifications_stub: MagicMock
) -> None:
    now = datetime.now(TZ)
    weather = WeatherSummary(
        city="Sélestat",
        temp_current=16.0,
        temp_min=10.0,
        temp_max=20.0,
        precipitation_mm=0.0,
        wind_kmh=12.0,
        description="ciel dégagé",
    )
    event = CalendarEvent(
        uid="u1",
        title="Réunion",
        start=now + timedelta(hours=2),
        end=now + timedelta(hours=3),
        location="Bureau",
        description=None,
        calendar_name="Personnel",
    )
    today_due = now.replace(hour=18, minute=0, second=0, microsecond=0)
    pending = [_make_task("acheter du pain", today_due)]

    deps.weather.get_today = AsyncMock(return_value=weather)
    deps.calendar.is_connected = True
    deps.calendar.list_all_upcoming = AsyncMock(return_value=[event])
    deps.tasks.list_pending = AsyncMock(return_value=pending)
    notifications_stub.count_unread = AsyncMock(return_value=3)

    snap = await build_dashboard(deps, notifications_stub)

    assert snap.weather is weather
    assert snap.next_event is event
    assert len(snap.today_tasks) == 1
    assert snap.unread_notifications == 3
    assert snap.latest_briefing is None


async def test_build_dashboard_weather_error_returns_none(
    deps: BotDeps, notifications_stub: MagicMock
) -> None:
    deps.weather.get_today = AsyncMock(side_effect=WeatherError("api down"))
    deps.calendar.is_connected = False
    deps.tasks.list_pending = AsyncMock(return_value=[])

    snap = await build_dashboard(deps, notifications_stub)

    assert snap.weather is None
    assert snap.next_event is None
    assert snap.today_tasks == []


async def test_build_dashboard_calendar_not_connected_returns_none_event(
    deps: BotDeps, notifications_stub: MagicMock
) -> None:
    weather = WeatherSummary(
        city="Sélestat",
        temp_current=16.0,
        temp_min=10.0,
        temp_max=20.0,
        precipitation_mm=0.0,
        wind_kmh=12.0,
        description="ciel dégagé",
    )
    deps.weather.get_today = AsyncMock(return_value=weather)
    deps.calendar.is_connected = False
    deps.tasks.list_pending = AsyncMock(return_value=[])

    snap = await build_dashboard(deps, notifications_stub)

    assert snap.weather is weather
    assert snap.next_event is None
    deps.calendar.list_all_upcoming.assert_not_called()


async def test_build_dashboard_calendar_error_returns_none_event(
    deps: BotDeps, notifications_stub: MagicMock
) -> None:
    deps.weather.get_today = AsyncMock(side_effect=WeatherError("down"))
    deps.calendar.is_connected = True
    deps.calendar.list_all_upcoming = AsyncMock(side_effect=ICloudCalendarError("offline"))
    deps.tasks.list_pending = AsyncMock(return_value=[])

    snap = await build_dashboard(deps, notifications_stub)

    assert snap.next_event is None


async def test_build_dashboard_calendar_empty_returns_none_event(
    deps: BotDeps, notifications_stub: MagicMock
) -> None:
    deps.weather.get_today = AsyncMock(side_effect=WeatherError("down"))
    deps.calendar.is_connected = True
    deps.calendar.list_all_upcoming = AsyncMock(return_value=[])
    deps.tasks.list_pending = AsyncMock(return_value=[])

    snap = await build_dashboard(deps, notifications_stub)

    assert snap.next_event is None


async def test_build_dashboard_weather_uses_home_when_no_location(
    deps: BotDeps, notifications_stub: MagicMock
) -> None:
    """Sans localisation connue, on utilise les coordonnées HOME_*."""
    weather = WeatherSummary(
        city="Sélestat",
        temp_current=16.0,
        temp_min=10.0,
        temp_max=20.0,
        precipitation_mm=0.0,
        wind_kmh=12.0,
        description="ciel dégagé",
    )
    deps.weather.get_today = AsyncMock(return_value=weather)
    deps.calendar.is_connected = False
    deps.tasks.list_pending = AsyncMock(return_value=[])
    # get_current_location renvoie None par défaut (fixture)

    await build_dashboard(deps, notifications_stub)

    deps.weather.get_today.assert_awaited_once()
    kwargs = deps.weather.get_today.await_args.kwargs
    assert kwargs["lat"] == 48.26
    assert kwargs["lon"] == 7.45
    assert kwargs["city"] == "Sélestat"


async def test_build_dashboard_weather_uses_work_when_at_work(
    deps: BotDeps, notifications_stub: MagicMock
) -> None:
    """Quand l'utilisateur est au bureau, on bascule sur les coords WORK_*."""
    from bot.locations.presence import LocationPresence

    weather = WeatherSummary(
        city="Obernai",
        temp_current=14.0,
        temp_min=8.0,
        temp_max=18.0,
        precipitation_mm=0.0,
        wind_kmh=8.0,
        description="couvert",
    )
    deps.weather.get_today = AsyncMock(return_value=weather)
    deps.calendar.is_connected = False
    deps.tasks.list_pending = AsyncMock(return_value=[])
    presence = LocationPresence(
        place="work",
        arrived_at=datetime.now(TZ),
        lat=None,
        lon=None,
    )
    deps.location_events.get_current_location = AsyncMock(return_value=presence)

    await build_dashboard(deps, notifications_stub)

    kwargs = deps.weather.get_today.await_args.kwargs
    assert kwargs["lat"] == 48.46
    assert kwargs["lon"] == 7.48
    assert kwargs["city"] == "Obernai"


async def test_build_dashboard_weather_uses_home_when_at_other_place(
    deps: BotDeps, notifications_stub: MagicMock
) -> None:
    """Un place inconnu (ni home ni work) retombe sur HOME_* (pas de géocoding)."""
    from bot.locations.presence import LocationPresence

    deps.weather.get_today = AsyncMock(side_effect=WeatherError("down"))
    deps.calendar.is_connected = False
    deps.tasks.list_pending = AsyncMock(return_value=[])
    presence = LocationPresence(
        place="sport",
        arrived_at=datetime.now(TZ),
        lat=None,
        lon=None,
    )
    deps.location_events.get_current_location = AsyncMock(return_value=presence)

    await build_dashboard(deps, notifications_stub)

    kwargs = deps.weather.get_today.await_args.kwargs
    assert kwargs["city"] == "Sélestat"


async def test_build_dashboard_uses_today_start_for_briefing_lookup(
    deps: BotDeps, notifications_stub: MagicMock
) -> None:
    """`latest_with_text_prefix` doit être appelé avec le début du jour local."""
    deps.weather.get_today = AsyncMock(side_effect=WeatherError("down"))
    deps.calendar.is_connected = False
    deps.tasks.list_pending = AsyncMock(return_value=[])

    await build_dashboard(deps, notifications_stub)

    notifications_stub.latest_with_text_prefix.assert_awaited_once()
    args = notifications_stub.latest_with_text_prefix.await_args
    assert args.args[0] == BRIEFING_TEXT_PREFIX
    expected_since = datetime.combine(datetime.now(TZ).date(), time.min, tzinfo=TZ)
    actual_since = args.kwargs["since"]
    # Tolérance d'une seconde sur le offset (now() est appelé deux fois).
    assert abs((actual_since - expected_since).total_seconds()) < 1.0
