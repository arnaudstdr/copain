"""Briefing matinal : météo + tâches + évènements + curation news IA."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from bot.briefing.weather import OpenMeteoClient, WeatherError, WeatherSummary
from bot.dashboard import BRIEFING_TEXT_PREFIX, today_tasks
from bot.llm.client import LLMError
from bot.logging_conf import get_logger

if TYPE_CHECKING:
    from bot.calendar.client import ICloudCalendarClient
    from bot.calendar.models import CalendarEvent
    from bot.config import Settings
    from bot.news.client import NewsCurator
    from bot.notifications.store import NotificationStore
    from bot.profile import UserProfile
    from bot.tasks.manager import TaskManager
    from bot.tasks.models import Task

log = get_logger(__name__)


class BriefingService:
    """Construit et envoie le briefing quotidien.

    Le cron job qui appelle `send_daily` est une closure enregistrée dans le
    MemoryJobStore de `ReminderScheduler` (cf. `add_cron_job`).
    """

    def __init__(
        self,
        settings: Settings,
        weather: OpenMeteoClient,
        tasks: TaskManager,
        news: NewsCurator,
        profile: UserProfile,
        calendar: ICloudCalendarClient,
        notifications: NotificationStore | None = None,
    ) -> None:
        self._settings = settings
        self._weather = weather
        self._tasks = tasks
        self._news = news
        self._profile = profile
        self._calendar = calendar
        self._notifications = notifications

    async def build(self) -> str:
        parts: list[str] = [BRIEFING_TEXT_PREFIX]

        try:
            weather = await self._weather.get_today(
                lat=self._settings.home_lat,
                lon=self._settings.home_lon,
                city=self._settings.home_city,
            )
            parts.append("\n" + _format_weather(weather))
        except WeatherError as exc:
            cause = exc.__cause__
            log.warning(
                "briefing_weather_skipped",
                error=str(exc),
                exc_type=type(cause).__name__ if cause is not None else "WeatherError",
            )
            parts.append("\n🌤 Météo indisponible pour le moment.")

        today_tasks = await self._today_tasks()
        parts.append("\n" + _format_tasks(today_tasks))

        today_events = await self._today_events()
        parts.append("\n" + _format_events(today_events))

        news_block = await self._news_block()
        if news_block:
            parts.append("\n" + news_block)

        return "\n".join(parts)

    async def send_daily(self) -> None:
        """Construit le briefing et l'empile dans la file `pending_notifications`."""
        if self._notifications is None:
            raise RuntimeError("BriefingService.send_daily() appelé sans NotificationStore injecté")
        text = await self.build()
        await self._notifications.add(text, title="☀️ Briefing du jour", priority=0, sound="morning")
        log.info("briefing_sent", chars=len(text))

    async def _today_events(self) -> list[CalendarEvent]:
        if not self._calendar.is_connected:
            return []
        try:
            return await self._calendar.list_all_today()
        except Exception as exc:
            log.warning("briefing_events_skipped", error=str(exc))
            return []

    async def _today_tasks(self) -> list[Task]:
        pending = await self._tasks.list_pending()
        return today_tasks(pending, ZoneInfo(self._settings.timezone))

    async def _news_block(self) -> str:
        """Lit `news_topics.daily_briefing` du profil + curation via NewsCurator.

        Structure attendue dans `data/profile.yaml` :

            news_topics:
              daily_briefing:
                - "LLM agents"
                - "OpenAI OR Anthropic"
              filters:
                domains_blocklist: [reddit.com, twitter.com]

        Si la section est absente ou vide → bloc news skip silencieusement.
        """
        topics, blocklist = _extract_news_config(self._profile.data)
        if not topics:
            log.info("briefing_news_skipped", reason="no_topics_in_profile")
            return ""

        try:
            summary = await self._news.fetch_top_news(topics=topics, domains_blocklist=blocklist)
        except LLMError as exc:
            log.warning("briefing_news_summary_failed", error=str(exc))
            return ""
        except Exception as exc:
            log.warning("briefing_news_failed", error=str(exc))
            return ""

        if not summary.strip():
            return ""
        return "🤖 *Actus IA des dernières 24h*\n" + summary


def _extract_news_config(
    profile_data: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Lit `news_topics.daily_briefing` et `news_topics.filters.domains_blocklist`.

    Retourne `([], [])` si la section est mal formée — on préfère un
    briefing sans news plutôt qu'un crash au démarrage 8h.
    """
    section = profile_data.get("news_topics") or {}
    if not isinstance(section, dict):
        return [], []
    raw_topics = section.get("daily_briefing") or []
    if not isinstance(raw_topics, list):
        return [], []
    topics = [str(t).strip() for t in raw_topics if str(t).strip()]
    filters = section.get("filters") or {}
    if not isinstance(filters, dict):
        return topics, []
    raw_block = filters.get("domains_blocklist") or []
    if not isinstance(raw_block, list):
        return topics, []
    blocklist = [str(d).strip() for d in raw_block if str(d).strip()]
    return topics, blocklist


def _format_weather(w: WeatherSummary) -> str:
    return (
        f"🌤 *Météo — {w.city}*\n"
        f"{w.description.capitalize()}, {w.temp_current:.0f}°C maintenant "
        f"(min {w.temp_min:.0f}°C / max {w.temp_max:.0f}°C)\n"
        f"Précipitations : {w.precipitation_mm:.0f} mm — Vent : {w.wind_kmh:.0f} km/h"
    )


def _format_tasks(tasks: Sequence[Task]) -> str:
    if not tasks:
        return "✅ *Tâches du jour*\nRien de prévu aujourd'hui."
    lines: list[str] = []
    for t in tasks:
        suffix = ""
        if t.due_at is not None:
            suffix = f" — {t.due_at.strftime('%H:%M')}"
        lines.append(f"- {t.content}{suffix}")
    return "📋 *Tâches du jour*\n" + "\n".join(lines)


def _format_events(events: Sequence[CalendarEvent]) -> str:
    if not events:
        return "📅 *Évènements du jour*\nAucun évènement prévu."
    lines = [
        f"- {e.start.strftime('%H:%M')}-{e.end.strftime('%H:%M')} {e.title}"
        + (f" ({e.location})" if e.location else "")
        for e in events
    ]
    return "📅 *Évènements du jour*\n" + "\n".join(lines)
