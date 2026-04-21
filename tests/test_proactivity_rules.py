"""Tests des règles pures `evaluate_rain` et `evaluate_upcoming_event`."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bot.briefing.weather import HourlyPrecipitation
from bot.calendar.models import CalendarEvent
from bot.proactivity.rules import evaluate_rain, evaluate_upcoming_event

TZ = ZoneInfo("Europe/Paris")


def _h(offset_hours: int = 0, mm: float = 0.0, proba: int = 0) -> HourlyPrecipitation:
    return HourlyPrecipitation(
        time=datetime.now(TZ) + timedelta(hours=offset_hours),
        mm=mm,
        probability_pct=proba,
    )


def _event(offset_min: int, uid: str = "e1", title: str = "Réunion") -> CalendarEvent:
    now = datetime.now(TZ)
    start = now + timedelta(minutes=offset_min)
    return CalendarEvent(
        uid=uid,
        title=title,
        start=start,
        end=start + timedelta(hours=1),
        location=None,
        description=None,
        calendar_name="Personnel",
    )


# ---------- evaluate_rain ----------


def test_rain_empty_list_returns_none() -> None:
    assert evaluate_rain([]) is None


def test_rain_under_both_thresholds_returns_none() -> None:
    hourly = [_h(mm=0.1, proba=30)]
    assert evaluate_rain(hourly) is None


def test_rain_above_mm_threshold_triggers() -> None:
    notif = evaluate_rain([_h(mm=0.5, proba=30)])
    assert notif is not None
    assert notif.kind == "rain"
    assert "Parapluie" in notif.text


def test_rain_above_proba_threshold_triggers() -> None:
    notif = evaluate_rain([_h(mm=0.0, proba=80)])
    assert notif is not None
    assert notif.kind == "rain"


def test_rain_looks_only_at_first_hour() -> None:
    """Les heures suivantes ne doivent pas déclencher (on veut prévenir tôt)."""
    hourly = [_h(offset_hours=0, mm=0.0, proba=10), _h(offset_hours=1, mm=5.0, proba=95)]
    assert evaluate_rain(hourly) is None


# ---------- evaluate_upcoming_event ----------


def test_event_empty_list_returns_none() -> None:
    assert evaluate_upcoming_event([], datetime.now(TZ)) is None


def test_event_in_window_triggers_with_uid() -> None:
    now = datetime.now(TZ)
    events = [_event(offset_min=60, uid="abc", title="Dentiste")]
    notif = evaluate_upcoming_event(events, now)
    assert notif is not None
    assert notif.kind == "event"
    assert notif.event_uid == "abc"
    assert "Dentiste" in notif.text


def test_event_too_early_is_ignored() -> None:
    now = datetime.now(TZ)
    events = [_event(offset_min=20, uid="x")]
    assert evaluate_upcoming_event(events, now) is None


def test_event_too_far_is_ignored() -> None:
    now = datetime.now(TZ)
    events = [_event(offset_min=120, uid="x")]
    assert evaluate_upcoming_event(events, now) is None


def test_event_picks_first_in_window() -> None:
    now = datetime.now(TZ)
    events = [
        _event(offset_min=30, uid="too-early"),
        _event(offset_min=60, uid="good"),
        _event(offset_min=70, uid="also-good"),
    ]
    notif = evaluate_upcoming_event(events, now)
    assert notif is not None
    assert notif.event_uid == "good"
