"""Briefing matinal : météo + tâches du jour + top 5 items RSS résumés."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from bot.briefing.weather import OpenMeteoClient, WeatherError, WeatherSummary
from bot.logging_conf import get_logger

if TYPE_CHECKING:
    from bot.calendar.client import ICloudCalendarClient
    from bot.calendar.models import CalendarEvent
    from bot.config import Settings
    from bot.llm.client import LLMClient
    from bot.rss.fetcher import FeedItem, RssFetcher
    from bot.rss.manager import FeedManager
    from bot.tasks.manager import TaskManager
    from bot.tasks.models import Task

log = get_logger(__name__)

TOP_RSS_ITEMS = 5


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
        rss: FeedManager,
        rss_fetcher: RssFetcher,
        llm: LLMClient,
        calendar: ICloudCalendarClient,
    ) -> None:
        self._settings = settings
        self._weather = weather
        self._tasks = tasks
        self._rss = rss
        self._rss_fetcher = rss_fetcher
        self._llm = llm
        self._calendar = calendar

    async def build(self) -> str:
        parts: list[str] = ["☀️ Bonjour ! Voici ton briefing du jour."]

        try:
            weather = await self._weather.get_today(
                lat=self._settings.home_lat,
                lon=self._settings.home_lon,
                city=self._settings.home_city,
            )
            parts.append("\n" + _format_weather(weather))
        except WeatherError as exc:
            log.warning("briefing_weather_skipped", error=str(exc))
            parts.append("\n🌤 Météo indisponible pour le moment.")

        today_tasks = await self._today_tasks()
        parts.append("\n" + _format_tasks(today_tasks))

        today_events = await self._today_events()
        parts.append("\n" + _format_events(today_events))

        rss_block = await self._rss_block()
        if rss_block:
            parts.append("\n" + rss_block)

        return "\n".join(parts)

    async def send_daily(self, chat_id: int) -> None:
        """Construit le briefing et l'envoie sur Telegram via un Bot éphémère."""
        from telegram import Bot

        text = await self.build()
        bot = Bot(token=self._settings.telegram_bot_token)
        async with bot:
            await bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
        log.info("briefing_sent", chat_id=chat_id, chars=len(text))

    async def _today_events(self) -> list[CalendarEvent]:
        if not self._calendar.is_connected:
            return []
        try:
            return await self._calendar.list_today()
        except Exception as exc:
            log.warning("briefing_events_skipped", error=str(exc))
            return []

    async def _today_tasks(self) -> list[Task]:
        pending = await self._tasks.list_pending()
        tz = ZoneInfo(self._settings.timezone)
        today = datetime.now(tz).date()
        todays: list[Task] = []
        for t in pending:
            if t.due_at is None:
                continue
            due = t.due_at if t.due_at.tzinfo else t.due_at.replace(tzinfo=tz)
            if due.astimezone(tz).date() == today:
                todays.append(t)
        return todays

    async def _rss_block(self) -> str:
        feeds = await self._rss.list(enabled_only=True)
        if not feeds:
            return ""
        items = await self._rss_fetcher.fetch_many(feeds, per_feed=5)
        top = items[:TOP_RSS_ITEMS]
        if not top:
            return ""
        summary = await self._summarize_items(top)
        return "📰 *Actus du jour*\n" + summary

    async def _summarize_items(self, items: Sequence[FeedItem]) -> str:
        bullets = "\n".join(
            f"- [{it.feed_name}] {it.title} ({it.url})\n  {it.summary[:300]}" for it in items
        )
        system = (
            "Tu es l'assistant d'Arnaud. Tu reçois une liste d'articles RSS récents. "
            "Pour chacun, écris un résumé factuel de 1 à 2 phrases en français en citant "
            "le flux source entre crochets et l'URL entre parenthèses. Sois concis. "
            "N'inclus PAS de bloc <meta>."
        )
        user = f"Articles :\n{bullets}"
        return await self._llm.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )


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
