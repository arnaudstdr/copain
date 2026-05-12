"""Agrégation d'état pour le tableau de bord PWA.

Ce module compose en un seul appel asynchrone toutes les sources visibles
sur l'écran principal de l'app iPhone (météo, prochain évènement iCloud,
tâches du jour, count des notifs non lues, dernier briefing du matin). Il
ne touche pas au LLM et n'a pas de side effects : l'endpoint `GET /dashboard`
peut être appelé à volonté pour rafraîchir l'UI après une action.

La logique `_today_tasks` est partagée avec `BriefingService` pour éviter
la divergence des règles "tâche du jour" entre le briefing 8h et la card
du dashboard.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from bot.briefing.weather import WeatherError, WeatherSummary
from bot.calendar.client import ICloudCalendarError
from bot.calendar.models import CalendarEvent
from bot.logging_conf import get_logger
from bot.notifications.models import PendingNotification
from bot.tasks.models import Task

if TYPE_CHECKING:
    from bot.notifications.store import NotificationStore
    from bot.pipeline import BotDeps

log = get_logger(__name__)

BRIEFING_TEXT_PREFIX = "☀️ Bonjour ! Voici ton briefing du jour."


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    """État instantané exposé par `GET /dashboard`."""

    weather: WeatherSummary | None
    next_event: CalendarEvent | None
    today_tasks: list[Task]
    unread_notifications: int
    latest_briefing: PendingNotification | None


def today_tasks(pending: Sequence[Task], tz: ZoneInfo) -> list[Task]:
    """Filtre la liste des tâches non terminées pour ne garder que celles du jour.

    Une tâche sans `due_at` est exclue (sinon elle apparaîtrait tous les jours
    dans le dashboard et dans le briefing). Une tâche avec `due_at` naïf est
    interprétée dans `tz` (compat avec les anciens enregistrements).
    """
    today = datetime.now(tz).date()
    result: list[Task] = []
    for t in pending:
        if t.due_at is None:
            continue
        due = t.due_at if t.due_at.tzinfo else t.due_at.replace(tzinfo=tz)
        if due.astimezone(tz).date() == today:
            result.append(t)
    return result


async def build_dashboard(deps: BotDeps, notifications: NotificationStore) -> DashboardSnapshot:
    """Agrège les cards du tableau de bord en un seul appel.

    Chaque source est tolérante aux pannes : un calendrier iCloud déconnecté,
    une API Open-Meteo down, etc. → la card concernée renvoie `None` et les
    autres restent peuplées. Les exceptions sont loggées en warning, jamais
    propagées : un dashboard partiel reste plus utile qu'une erreur HTTP.
    """
    tz = ZoneInfo(deps.settings.timezone)

    weather = await _safe_weather(deps)
    next_event = await _safe_next_event(deps)
    pending = await deps.tasks.list_pending()
    today_tasks_list = today_tasks(pending, tz)
    unread = await notifications.count_unread()
    today_start = datetime.combine(datetime.now(tz).date(), time.min, tzinfo=tz)
    latest_briefing = await notifications.latest_with_text_prefix(
        BRIEFING_TEXT_PREFIX, since=today_start
    )

    log.info(
        "dashboard_built",
        weather=weather is not None,
        next_event=next_event is not None,
        tasks=len(today_tasks_list),
        unread=unread,
        briefing=latest_briefing is not None,
    )
    return DashboardSnapshot(
        weather=weather,
        next_event=next_event,
        today_tasks=today_tasks_list,
        unread_notifications=unread,
        latest_briefing=latest_briefing,
    )


async def _safe_weather(deps: BotDeps) -> WeatherSummary | None:
    try:
        return await deps.weather.get_today(
            lat=deps.settings.home_lat,
            lon=deps.settings.home_lon,
            city=deps.settings.home_city,
        )
    except WeatherError as exc:
        log.warning("dashboard_weather_skipped", error=str(exc))
        return None


async def _safe_next_event(deps: BotDeps) -> CalendarEvent | None:
    if not deps.calendar.is_connected:
        return None
    try:
        events = await deps.calendar.list_all_upcoming(days=1)
    except ICloudCalendarError as exc:
        log.warning("dashboard_calendar_skipped", error=str(exc))
        return None
    return events[0] if events else None
