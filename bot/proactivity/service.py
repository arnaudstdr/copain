"""Service de proactivité : évalue les règles et pousse au plus 1 notif par tick.

Garde-fous appliqués dans l'ordre :
1. Feature flag `settings.proactivity_enabled` (sinon tick immédiat no-op).
2. Fenêtre horaire locale `[window_start_hour, window_end_hour[`.
3. Budget quotidien (nombre d'entrées `NotificationLog` depuis minuit local).
4. Dédup : event déjà notifié (même `event_uid`) / pluie récente (cooldown).
5. Priorité event > pluie quand les deux déclenchent au même tick.

Toute exception à l'intérieur de `tick()` est catchée et loggée — une panne
iCloud ou Open-Meteo ne doit jamais tuer le job APScheduler.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.sql import functions as sql_functions

from bot.briefing.weather import WeatherError
from bot.logging_conf import get_logger
from bot.proactivity.models import NotificationLog
from bot.proactivity.rules import Notification, evaluate_rain, evaluate_upcoming_event
from bot.telegram_sender import send_message

if TYPE_CHECKING:
    from bot.briefing.weather import OpenMeteoClient
    from bot.calendar.client import ICloudCalendarClient
    from bot.config import Settings

log = get_logger(__name__)

SendFn = Callable[[int, str], Awaitable[None]]


class ProactivityService:
    def __init__(
        self,
        *,
        settings: Settings,
        weather: OpenMeteoClient,
        calendar: ICloudCalendarClient,
        engine: AsyncEngine,
        chat_id: int,
        send: SendFn = send_message,
    ) -> None:
        self._settings = settings
        self._weather = weather
        self._calendar = calendar
        self._engine = engine
        self._sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        self._chat_id = chat_id
        self._send = send
        self._tz = ZoneInfo(settings.timezone)

    async def tick(self) -> None:
        """Un passage : évalue les règles, envoie au plus une notif."""
        try:
            await self._tick_inner()
        except Exception as exc:
            log.exception("proactivity_tick_failed", error=str(exc))

    async def _tick_inner(self) -> None:
        s = self._settings
        if not s.proactivity_enabled:
            return

        now = datetime.now(self._tz)
        if not (s.proactivity_window_start_hour <= now.hour < s.proactivity_window_end_hour):
            log.debug("proactivity_tick_skipped", reason="out_of_window", hour=now.hour)
            return

        sent_today = await self._count_sent_today(now)
        if sent_today >= s.proactivity_daily_budget:
            log.debug(
                "proactivity_tick_skipped",
                reason="budget_reached",
                sent_today=sent_today,
                budget=s.proactivity_daily_budget,
            )
            return

        # Priorité : event > pluie (une seule notif par tick).
        event_notif = await self._evaluate_event(now)
        if event_notif is not None:
            await self._dispatch(event_notif)
            return

        rain_notif = await self._evaluate_rain(now)
        if rain_notif is not None:
            await self._dispatch(rain_notif)

    async def _evaluate_event(self, now: datetime) -> Notification | None:
        if not self._calendar.is_connected:
            return None
        try:
            events = await self._calendar.list_all_between(now, now + timedelta(minutes=90))
        except Exception as exc:
            log.warning("proactivity_calendar_failed", error=str(exc))
            return None
        notif = evaluate_upcoming_event(events, now)
        if notif is None or notif.event_uid is None:
            return notif
        if await self._event_already_notified(notif.event_uid):
            log.debug("proactivity_event_already_notified", uid=notif.event_uid)
            return None
        return notif

    async def _evaluate_rain(self, now: datetime) -> Notification | None:
        try:
            hourly = await self._weather.get_hourly_precipitation(
                lat=self._settings.home_lat,
                lon=self._settings.home_lon,
                hours_ahead=2,
            )
        except WeatherError as exc:
            cause = exc.__cause__
            log.warning(
                "proactivity_weather_failed",
                error=str(exc),
                exc_type=type(cause).__name__ if cause is not None else "WeatherError",
            )
            return None
        except Exception as exc:
            log.warning(
                "proactivity_weather_failed",
                error=str(exc),
                exc_type=type(exc).__name__,
            )
            return None
        notif = evaluate_rain(hourly)
        if notif is None:
            return None
        cooldown = timedelta(hours=self._settings.proactivity_rain_cooldown_hours)
        last_rain = await self._last_sent_at(kind="rain")
        if last_rain is not None and (now - last_rain) < cooldown:
            log.debug("proactivity_rain_cooldown", last=last_rain.isoformat())
            return None
        return notif

    async def _dispatch(self, notif: Notification) -> None:
        await self._send(self._chat_id, notif.text)
        async with self._sessionmaker() as session:
            session.add(NotificationLog(kind=notif.kind, event_uid=notif.event_uid))
            await session.commit()
        log.info("proactivity_sent", kind=notif.kind, uid=notif.event_uid)

    async def _count_sent_today(self, now: datetime) -> int:
        """Compte les notifs envoyées depuis minuit local.

        `sent_at` est stocké en UTC par SQLAlchemy `DateTime(timezone=True)`
        sur SQLite (qui ne préserve pas la tz). On convertit donc le seuil
        « minuit local » en UTC avant la requête.
        """
        midnight_local = datetime.combine(now.date(), time.min, tzinfo=self._tz)
        midnight_utc_naive = midnight_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        async with self._sessionmaker() as session:
            stmt = (
                select(sql_functions.count())
                .select_from(NotificationLog)
                .where(NotificationLog.sent_at >= midnight_utc_naive)
            )
            result = await session.execute(stmt)
            return int(result.scalar_one())

    async def _event_already_notified(self, event_uid: str) -> bool:
        async with self._sessionmaker() as session:
            stmt = (
                select(NotificationLog.id)
                .where(NotificationLog.kind == "event", NotificationLog.event_uid == event_uid)
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None

    async def _last_sent_at(self, *, kind: str) -> datetime | None:
        async with self._sessionmaker() as session:
            stmt = (
                select(NotificationLog.sent_at)
                .where(NotificationLog.kind == kind)
                .order_by(NotificationLog.sent_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            last = result.scalar_one_or_none()
        if last is None:
            return None
        # SQLite ne préserve pas la tz : on réattache UTC pour comparer à `now`.
        return last if last.tzinfo is not None else last.replace(tzinfo=ZoneInfo("UTC"))  # type: ignore[no-any-return]
