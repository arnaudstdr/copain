"""Tests unitaires du module `bot.dashboard`.

Couvre la logique d'agrégation (today_tasks, build_dashboard) en isolation
des couches HTTP et SQLite. Les tests E2E de `GET /dashboard` vivent dans
`tests/test_api.py`.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from bot.briefing.weather import WeatherError, WeatherSummary
from bot.calendar.client import ICloudCalendarError
from bot.calendar.models import CalendarEvent
from bot.dashboard import build_dashboard, overdue_tasks_count, today_tasks
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

    expenses = MagicMock()
    expenses.list_for_month = AsyncMock(return_value=[])
    expenses.list_for_cycle = AsyncMock(return_value=[])
    expenses.list_savings_for_year = AsyncMock(return_value=[])
    expenses.is_recurring_ticked_this_month = AsyncMock(return_value=False)
    expenses.is_recurring_ticked_in_cycle = AsyncMock(return_value=False)
    # Aucune ancre déclarée → bornes mois civil (comportement fallback).
    expenses.current_cycle_bounds = AsyncMock(
        side_effect=lambda today: (
            today.replace(day=1),
            (
                today.replace(year=today.year + 1, month=1, day=1)
                if today.month == 12
                else today.replace(month=today.month + 1, day=1)
            ),
        )
    )

    return BotDeps(
        settings=settings,
        llm=MagicMock(),
        memory=MagicMock(),
        tasks=MagicMock(),
        thoughts=MagicMock(),
        expenses=expenses,
        scheduler=MagicMock(),
        search=MagicMock(),
        rss=MagicMock(),
        rss_fetcher=MagicMock(),
        calendar=MagicMock(),
        fuel=MagicMock(),
        geocoder=MagicMock(),
        weather=MagicMock(),
        news=MagicMock(),
        profile=UserProfile(raw_yaml="", is_loaded=False),
        location_events=location_events,
        proactivity=MagicMock(),
        history=deque(maxlen=6),
    )


@pytest.fixture
def notifications_stub() -> MagicMock:
    stub = MagicMock()
    stub.count_unread = AsyncMock(return_value=0)
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


# --- overdue_tasks_count ----------------------------------------------------


def test_overdue_tasks_count_strict_past_day() -> None:
    now = datetime.now(TZ)
    today_morning = now.replace(hour=6, minute=0, second=0, microsecond=0)
    yesterday = today_morning - timedelta(days=1)
    pending = [
        _make_task("hier", yesterday),  # en retard
        _make_task("avant-hier", yesterday - timedelta(days=1)),  # en retard
        _make_task("aujourd'hui matin", today_morning),  # pas en retard (jour J)
        _make_task("demain", today_morning + timedelta(days=1)),  # pas en retard
        _make_task("sans due", None),  # exclue
    ]
    assert overdue_tasks_count(pending, TZ) == 2


def test_overdue_tasks_count_empty_returns_zero() -> None:
    assert overdue_tasks_count([], TZ) == 0


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


# --- Budget -----------------------------------------------------------------


def _profile_with_finance(*, with_loyer: bool = True) -> UserProfile:
    if not with_loyer:
        return UserProfile(raw_yaml="", is_loaded=False)
    return UserProfile(
        raw_yaml="",
        is_loaded=True,
        data={
            "finances": {
                "currency": "EUR",
                "recurring": [
                    {
                        "key": "loyer",
                        "label": "Loyer",
                        "amount": 800,
                        "day": 5,
                        "kind": "expense",
                    }
                ],
            }
        },
    )


async def test_build_dashboard_budget_is_none_when_yaml_missing(
    deps: BotDeps, notifications_stub: MagicMock
) -> None:
    deps.weather.get_today = AsyncMock(side_effect=WeatherError("x"))
    deps.calendar.is_connected = False
    deps.tasks.list_pending = AsyncMock(return_value=[])
    snap = await build_dashboard(deps, notifications_stub)
    assert snap.budget is None


async def test_build_dashboard_budget_is_none_when_manager_raises(
    deps: BotDeps, notifications_stub: MagicMock
) -> None:
    deps.profile = _profile_with_finance()
    deps.expenses.list_for_cycle = AsyncMock(side_effect=RuntimeError("db down"))
    deps.weather.get_today = AsyncMock(side_effect=WeatherError("x"))
    deps.calendar.is_connected = False
    deps.tasks.list_pending = AsyncMock(return_value=[])
    snap = await build_dashboard(deps, notifications_stub)
    assert snap.budget is None


async def test_build_dashboard_budget_present_when_configured(
    deps: BotDeps, notifications_stub: MagicMock
) -> None:
    deps.profile = _profile_with_finance()
    deps.weather.get_today = AsyncMock(side_effect=WeatherError("x"))
    deps.calendar.is_connected = False
    deps.tasks.list_pending = AsyncMock(return_value=[])
    deps.expenses.list_for_cycle = AsyncMock(return_value=[])
    deps.expenses.list_savings_for_year = AsyncMock(return_value=[])
    snap = await build_dashboard(deps, notifications_stub)
    assert snap.budget is not None
    assert snap.budget.pending_recurring_count == 1
