"""Agrégation d'état pour le tableau de bord PWA.

Ce module compose en un seul appel asynchrone toutes les sources visibles
sur l'écran principal de l'app iPhone (météo, prochain évènement iCloud,
tâches du jour, count des notifs non lues). Il ne touche pas au LLM et n'a
pas de side effects : l'endpoint `GET /dashboard` peut être appelé à volonté
pour rafraîchir l'UI après une action.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from bot.calendar.client import ICloudCalendarError
from bot.calendar.models import CalendarEvent
from bot.finance.budget import BudgetSummary, compute_budget
from bot.finance.config import extract_finance_config
from bot.logging_conf import get_logger
from bot.tasks.models import Task
from bot.weather.client import WeatherError, WeatherSummary

if TYPE_CHECKING:
    from bot.notifications.store import NotificationStore
    from bot.pipeline import BotDeps

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    """État instantané exposé par `GET /dashboard`."""

    weather: WeatherSummary | None
    next_event: CalendarEvent | None
    today_tasks: list[Task]
    overdue_tasks_count: int
    unread_notifications: int
    budget: BudgetSummary | None


def today_tasks(pending: Sequence[Task], tz: ZoneInfo) -> list[Task]:
    """Filtre la liste des tâches non terminées pour ne garder que celles du jour.

    Une tâche sans `due_at` est exclue (sinon elle apparaîtrait tous les jours
    dans le dashboard). Une tâche avec `due_at` naïf est interprétée dans
    `tz` (compat avec les anciens enregistrements).
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


def overdue_tasks_count(pending: Sequence[Task], tz: ZoneInfo) -> int:
    """Compte les tâches non terminées dont la date due est strictement avant aujourd'hui.

    Critère "jour calendaire de retard" (cohérent avec le badge "En retard de
    N jours" affiché dans l'overlay des tâches). Les tâches sans `due_at` ne
    comptent pas, et celles dues à une heure passée du jour courant non plus
    (elles restent dans `today_tasks`).
    """
    today = datetime.now(tz).date()
    n = 0
    for t in pending:
        if t.due_at is None:
            continue
        due = t.due_at if t.due_at.tzinfo else t.due_at.replace(tzinfo=tz)
        if due.astimezone(tz).date() < today:
            n += 1
    return n


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
    overdue_count = overdue_tasks_count(pending, tz)
    unread = await notifications.count_unread()
    budget = await _safe_budget_summary(deps)

    log.info(
        "dashboard_built",
        weather=weather is not None,
        next_event=next_event is not None,
        tasks=len(today_tasks_list),
        overdue=overdue_count,
        unread=unread,
        budget=budget is not None,
    )
    return DashboardSnapshot(
        weather=weather,
        next_event=next_event,
        today_tasks=today_tasks_list,
        overdue_tasks_count=overdue_count,
        unread_notifications=unread,
        budget=budget,
    )


async def _safe_weather(deps: BotDeps) -> WeatherSummary | None:
    """Récupère la météo du jour, contextualisée à la localisation courante.

    Si l'utilisateur est au bureau (place="work"), on utilise les coords
    WORK_*. Sinon (présence inconnue ou maison), on retombe sur HOME_*.
    Les autres labels personnalisés tombent aussi sur HOME — on ne fait
    pas de géocoding inverse à la volée pour rester rapide et offline.
    """
    presence = await deps.location_events.get_current_location()
    if presence is not None and presence.place == "work":
        lat = deps.settings.work_lat
        lon = deps.settings.work_lon
        city = deps.settings.work_city
    else:
        lat = deps.settings.home_lat
        lon = deps.settings.home_lon
        city = deps.settings.home_city
    try:
        return await deps.weather.get_today(lat=lat, lon=lon, city=city)
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


async def _safe_budget_summary(deps: BotDeps) -> BudgetSummary | None:
    """Calcule l'état budgétaire courant.

    Si la section `finances` du YAML est absente ou si une erreur survient
    (YAML mal formé, SQLite indisponible), retourne `None` et la card côté
    front affichera un état vide. Pas de propagation : le reste du
    dashboard reste utilisable.
    """
    try:
        cfg = extract_finance_config(deps.profile.data)
        if not cfg.is_configured:
            return None
        today_d = datetime.now(ZoneInfo(deps.settings.timezone)).date()
        cycle_start, cycle_end = await deps.expenses.current_cycle_bounds(today_d)
        cycle_rows = await deps.expenses.list_for_cycle(today_d)
        year_savings = await deps.expenses.list_savings_for_year(today_d.year)
        return compute_budget(
            config=cfg,
            month_expenses=cycle_rows,
            year_savings=year_savings,
            today=today_d,
            cycle_start=cycle_start,
            cycle_end=cycle_end,
        )
    except Exception as exc:
        log.warning("dashboard_budget_skipped", error=str(exc))
        return None
