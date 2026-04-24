"""Handler message entrant principal — orchestre LLM + mémoire + tâches + recherche + RSS."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import dateparser

from bot.briefing.weather import WeatherError
from bot.calendar.client import ICloudCalendarError
from bot.fuel.client import FuelError
from bot.fuel.geocoding import NominatimError
from bot.fuel.models import FUEL_LABELS, GeoPoint, normalize_fuel_type
from bot.llm.client import LLMError, LLMTimeoutError
from bot.llm.parser import Meta, MetaParseError, extract_meta
from bot.llm.prompt import build_system_prompt
from bot.logging_conf import get_logger
from bot.rss.manager import FeedAlreadyExists
from bot.security import is_allowed
from bot.telegram_sender import reply_markdown

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

    from bot.briefing.service import BriefingService
    from bot.briefing.weather import DailyWeather, OpenMeteoClient
    from bot.calendar.client import ICloudCalendarClient
    from bot.config import Settings
    from bot.fuel.client import FuelClient
    from bot.fuel.geocoding import NominatimClient
    from bot.fuel.models import FuelStation
    from bot.llm.client import LLMClient
    from bot.memory.manager import MemoryManager
    from bot.rss.fetcher import FeedItem, RssFetcher
    from bot.rss.manager import FeedManager
    from bot.rss.models import Feed
    from bot.search.searxng import SearxngClient
    from bot.tasks.manager import TaskManager
    from bot.tasks.scheduler import ReminderScheduler

log = get_logger(__name__)

MAX_HISTORY = 6
FALLBACK_TEXT = (
    "J'ai eu un souci pour interpréter la réponse, mais je suis là. Redis-moi ça autrement ?"
)


@dataclass
class BotDeps:
    """Conteneur pour toutes les dépendances injectées dans le handler."""

    settings: Settings
    llm: LLMClient
    memory: MemoryManager
    tasks: TaskManager
    scheduler: ReminderScheduler
    search: SearxngClient
    rss: FeedManager
    rss_fetcher: RssFetcher
    briefing: BriefingService
    calendar: ICloudCalendarClient
    fuel: FuelClient
    geocoder: NominatimClient
    weather: OpenMeteoClient
    history: deque[str]


HandlerFn = Callable[["Update", "ContextTypes.DEFAULT_TYPE"], Coroutine[Any, Any, None]]


def make_handler(deps: BotDeps) -> HandlerFn:
    """Retourne la coroutine handler texte à enregistrer dans python-telegram-bot."""

    async def handle_message(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if not is_allowed(update, deps.settings.allowed_user_id):
            return

        message = update.message
        if message is None or message.text is None:
            return

        user_text = message.text
        chat_id = message.chat_id
        log.info("message_received", chat_id=chat_id, preview=user_text[:80])

        try:
            reply = await _process(user_text, chat_id, deps)
        except LLMTimeoutError:
            log.warning("llm_timeout", chat_id=chat_id)
            reply = (
                "Le modèle met trop longtemps à répondre pour l'instant. "
                "Réessaie dans quelques secondes."
            )
        except LLMError as exc:
            log.error("llm_error", chat_id=chat_id, error=str(exc))
            reply = "Le modèle LLM a un souci côté serveur pour l'instant. Réessaie dans un moment."
        except Exception as exc:
            log.exception("handler_failed", error=str(exc))
            reply = "Désolé, une erreur interne est survenue."

        await reply_markdown(message, reply)

    return handle_message


def make_photo_handler(deps: BotDeps) -> HandlerFn:
    """Retourne le handler pour les messages PHOTO (multimodal via Ollama)."""

    async def handle_photo(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        if not is_allowed(update, deps.settings.allowed_user_id):
            return

        message = update.message
        if message is None or not message.photo:
            return

        caption = message.caption or ""
        chat_id = message.chat_id
        # On télécharge la plus grande résolution disponible (dernière de la liste).
        largest = message.photo[-1]
        tg_file = await largest.get_file()
        image_bytes = bytes(await tg_file.download_as_bytearray())
        log.info(
            "photo_received",
            chat_id=chat_id,
            size=len(image_bytes),
            caption_preview=caption[:80],
        )

        try:
            reply = await _process(caption, chat_id, deps, images=[image_bytes])
        except LLMTimeoutError:
            log.warning("llm_timeout", chat_id=chat_id, kind="photo")
            reply = (
                "Le modèle met trop longtemps à analyser l'image. Réessaie dans quelques secondes."
            )
        except LLMError as exc:
            log.error("llm_error", chat_id=chat_id, kind="photo", error=str(exc))
            reply = "Le modèle LLM a un souci côté serveur pour l'instant. Réessaie dans un moment."
        except Exception as exc:
            log.exception("photo_handler_failed", error=str(exc))
            reply = "Désolé, je n'ai pas réussi à analyser cette image."

        await reply_markdown(message, reply)

    return handle_photo


async def _process(
    user_text: str,
    chat_id: int,
    deps: BotDeps,
    images: list[bytes] | None = None,
) -> str:
    memory_context = await deps.memory.retrieve_context(
        user_text or "(image envoyée sans légende)", top_k=5
    )
    tz = ZoneInfo(deps.settings.timezone)
    now_str = datetime.now(tz).strftime("%A %d %B %Y à %H:%M")
    system_prompt = build_system_prompt(
        memory_context=memory_context,
        recent_history=list(deps.history),
        current_datetime=now_str,
        home_city=deps.settings.home_city,
    )

    user_content = (
        user_text if user_text else "Analyse cette image et propose une action pertinente."
    )
    raw = await deps.llm.call(system=system_prompt, user=user_content, images=images)

    try:
        text, meta = extract_meta(raw)
    except MetaParseError as exc:
        log.warning("meta_parse_failed", error=str(exc), raw_preview=raw[:200])
        return FALLBACK_TEXT

    await _apply_side_effects(user_text, chat_id, meta, deps)

    if meta["intent"] == "search" and meta["search_query"]:
        results = await deps.search.search(meta["search_query"])
        log.info("search_performed", query=meta["search_query"], hits=len(results))
        text = await deps.llm.call_with_search(user_text, results)

    elif meta["intent"] == "feed" and meta["feed"]["action"]:
        text = await _handle_feed(user_text, meta, deps, intro=text)

    elif meta["intent"] == "event" and meta["event"]["action"]:
        text = await _handle_event(meta, deps, intro=text)

    elif meta["intent"] == "fuel" and meta["fuel"]["fuel_type"]:
        text = await _handle_fuel(meta, deps, intro=text)

    elif meta["intent"] == "weather":
        text = await _handle_weather(meta, deps, intro=text)

    history_user = user_text if user_text else "(image envoyée)"
    if images:
        history_user = f"[photo] {history_user}"
    # deps.history est un deque(maxlen=MAX_HISTORY) créé dans main.py :
    # la troncature est atomique, pas de boucle pop manuelle à maintenir.
    deps.history.append(f"user: {history_user}")
    deps.history.append(f"assistant: {text}")

    return text


async def _apply_side_effects(
    user_text: str,
    chat_id: int,
    meta: Meta,
    deps: BotDeps,
) -> None:
    if meta["store_memory"] and meta["memory_content"]:
        await deps.memory.store(
            original_message=user_text,
            memory_content=meta["memory_content"],
        )

    if meta["intent"] == "task" and meta["task"]["content"]:
        due_dt = _parse_due(meta["task"]["due_str"], deps.settings.timezone)
        task = await deps.tasks.create(content=meta["task"]["content"], due_at=due_dt)
        log.info(
            "task_created",
            task_id=task.id,
            due_str=meta["task"]["due_str"],
            due_at=due_dt.isoformat() if due_dt else None,
        )
        if due_dt is not None:
            deps.scheduler.add_reminder(
                task_id=task.id,
                due_at=due_dt,
                chat_id=chat_id,
                content=task.content,
            )


async def _handle_feed(user_text: str, meta: Meta, deps: BotDeps, intro: str) -> str:
    action = meta["feed"]["action"]
    name = meta["feed"]["name"]
    url = meta["feed"]["url"]
    log.info("feed_action", action=action, name=name, url=url)

    if action == "add":
        if not name or not url:
            return "Il me faut un nom et une URL pour ajouter un flux."
        try:
            feed = await deps.rss.add(url=url, name=name)
        except FeedAlreadyExists:
            return f"Le flux « {name} » existe déjà."
        return f"Flux ajouté : {feed.name} ({feed.url})"

    if action == "list":
        feeds = await deps.rss.list(enabled_only=False)
        if not feeds:
            return "Aucun flux enregistré."
        lines = [f"- {f.name} [{f.category}] {'✓' if f.enabled else '✗'} — {f.url}" for f in feeds]
        return "Tes flux :\n" + "\n".join(lines)

    if action == "remove":
        if not name:
            return "Dis-moi quel flux supprimer."
        ok = await deps.rss.remove(name)
        return f"Flux « {name} » supprimé." if ok else f"Aucun flux trouvé avec le nom « {name} »."

    if action == "summarize":
        target_feeds: Sequence[Feed]
        if name:
            single = await deps.rss.get(name)
            if single is None:
                return f"Aucun flux trouvé pour « {name} »."
            target_feeds = [single]
        else:
            target_feeds = await deps.rss.list(enabled_only=True)
            if not target_feeds:
                return "Aucun flux actif à résumer."

        items = await deps.rss_fetcher.fetch_many(target_feeds, per_feed=5)
        if not items:
            return "Aucun article récupéré pour le moment."
        summary = await _summarize_feed_items(deps, user_text, items[:10])
        return summary if intro.strip() in ("", FALLBACK_TEXT) else f"{intro}\n\n{summary}"

    return intro


async def _summarize_feed_items(deps: BotDeps, user_text: str, items: Sequence[FeedItem]) -> str:
    bullets = "\n".join(
        f"- [{it.feed_name}] {it.title} ({it.url})\n  {it.summary[:300]}" for it in items
    )
    system = (
        "Tu es l'assistant personnel d'Arnaud. Tu reçois une liste d'articles RSS récents. "
        "Résume-les en français : 1 à 2 lignes par article, en citant le flux source et l'URL. "
        "Sois factuel et concis. N'inclus PAS de bloc <meta>."
    )
    user = f"Question initiale : {user_text}\n\nArticles :\n{bullets}"
    return await deps.llm.chat(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )


async def _handle_event(meta: Meta, deps: BotDeps, intro: str) -> str:
    action = meta["event"]["action"]
    log.info(
        "event_action",
        action=action,
        title=meta["event"]["title"],
        start_str=meta["event"]["start_str"],
        end_str=meta["event"]["end_str"],
        calendar_name=meta["event"]["calendar_name"],
        range_str=meta["event"]["range_str"],
    )

    if not deps.calendar.is_connected:
        return "Le calendrier iCloud n'est pas disponible pour le moment."

    if action == "create":
        title = meta["event"]["title"]
        start_str = meta["event"]["start_str"]
        if not title or not start_str:
            return "Il me faut au minimum un titre et une heure pour créer un événement."

        tz_name = deps.settings.timezone
        start = _parse_due(start_str, tz_name)
        if start is None:
            return f"Impossible d'interpréter « {start_str} » comme une date."
        end = _parse_due(meta["event"]["end_str"], tz_name)
        if end is None:
            end = start + timedelta(hours=1)
        log.info(
            "event_times_parsed",
            start_str=start_str,
            end_str=meta["event"]["end_str"],
            start=start.isoformat(),
            end=end.isoformat(),
        )
        try:
            event = await deps.calendar.create_event(
                title=title,
                start=start,
                end=end,
                location=meta["event"]["location"],
                description=meta["event"]["description"],
                calendar_name=meta["event"]["calendar_name"],
            )
        except ICloudCalendarError:
            log.exception("calendar_create_failed")
            return "Désolé, impossible de créer l'évènement pour le moment."
        confirm = (
            f"📅 Ajouté au calendrier : {event.title} — "
            f"{event.start.strftime('%A %d %B à %H:%M')} ({event.calendar_name})"
        )
        return confirm if intro.strip() in ("", FALLBACK_TEXT) else f"{intro}\n{confirm}"

    if action == "list":
        tz = ZoneInfo(deps.settings.timezone)
        range_str = meta["event"]["range_str"]
        start, end = _parse_range(range_str, tz)
        try:
            events = await deps.calendar.list_all_between(start, end)
        except ICloudCalendarError:
            log.exception("calendar_list_failed")
            return "Désolé, lecture du calendrier impossible pour le moment."
        if not events:
            return f"Aucun évènement sur {range_str or 'la période demandée'}."
        lines = [
            f"- {e.start.strftime('%a %d/%m %H:%M')}-{e.end.strftime('%H:%M')} "
            f"{e.title}" + (f" ({e.location})" if e.location else "")
            for e in events
        ]
        header = f"📅 Évènements ({range_str or 'à venir'})"
        return f"{header}\n" + "\n".join(lines)

    return intro


async def _handle_fuel(meta: Meta, deps: BotDeps, intro: str) -> str:
    raw_type = meta["fuel"]["fuel_type"]
    fuel_type = normalize_fuel_type(raw_type)
    if fuel_type is None:
        return (
            f"Je ne reconnais pas le carburant « {raw_type} ». "
            "Essaie : gazole, SP95, SP98, E10, E85 ou GPLc."
        )

    location_query = meta["fuel"]["location"]
    radius_km = meta["fuel"]["radius_km"] or deps.settings.fuel_default_radius_km
    log.info(
        "fuel_action",
        fuel_type=fuel_type,
        radius_km=radius_km,
        location=location_query,
    )

    if location_query:
        try:
            geocoded = await deps.geocoder.geocode_fr(location_query)
        except NominatimError:
            log.exception("geocode_failed")
            return "Désolé, impossible de localiser ce lieu pour l'instant."
        if geocoded is None:
            return f"Je n'ai pas trouvé « {location_query} » sur la carte."
        center = geocoded
        place_label = location_query
    else:
        center = GeoPoint(lat=deps.settings.home_lat, lon=deps.settings.home_lon)
        place_label = deps.settings.home_city

    try:
        stations = await deps.fuel.find_cheapest(
            fuel_type=fuel_type,
            center=center,
            radius_km=radius_km,
            limit=5,
        )
    except FuelError:
        log.exception("fuel_fetch_failed")
        return "Désolé, impossible de récupérer les prix des carburants pour l'instant."

    if not stations:
        return (
            f"Aucune station trouvée pour le {FUEL_LABELS[fuel_type]} "
            f"dans un rayon de {_format_km(radius_km)} autour de {place_label}."
        )

    tz = ZoneInfo(deps.settings.timezone)
    header = (
        f"⛽ Top {len(stations)} {FUEL_LABELS[fuel_type]} "
        f"(rayon {_format_km(radius_km)} autour de {place_label})"
    )
    lines = [_format_station(i, s) for i, s in enumerate(stations, start=1)]
    freshness = _format_freshness(stations, tz)
    body = f"{header}\n" + "\n".join(lines)
    return body + (f"\n{freshness}" if freshness else "")


async def _handle_weather(meta: Meta, deps: BotDeps, intro: str) -> str:
    location_query = meta["weather"]["location"]
    when_str = meta["weather"]["when"]
    log.info("weather_action", location=location_query, when=when_str)

    if location_query:
        try:
            geocoded = await deps.geocoder.geocode_fr(location_query)
        except NominatimError:
            log.exception("weather_geocode_failed")
            return "Désolé, impossible de localiser ce lieu pour l'instant."
        if geocoded is None:
            return f"Je n'ai pas trouvé « {location_query} » sur la carte."
        lat, lon, label = geocoded.lat, geocoded.lon, location_query
    else:
        lat = deps.settings.home_lat
        lon = deps.settings.home_lon
        label = deps.settings.home_city

    tz = ZoneInfo(deps.settings.timezone)
    start_offset, end_offset = _parse_weather_range(when_str, tz)
    # +1 pour inclure la borne haute, plafond à 16 (limite Open-Meteo).
    days_needed = min(end_offset + 1, 16)

    try:
        forecast = await deps.weather.get_forecast(lat=lat, lon=lon, city=label, days=days_needed)
    except WeatherError:
        log.exception("weather_fetch_failed")
        return "Désolé, impossible de récupérer la météo pour l'instant."

    if not forecast:
        return f"Aucune prévision disponible pour {label}."

    selected = forecast[start_offset : end_offset + 1]
    if not selected:
        return f"Aucune prévision disponible pour la période demandée à {label}."

    period_label = _weather_period_label(when_str)
    if len(selected) == 1:
        return _format_weather_single(selected[0], label, period_label)
    return _format_weather_multi(selected, label, period_label)


def _format_weather_single(day: DailyWeather, place: str, period: str) -> str:
    header = f"🌤 *Météo — {place}* ({period})"
    current_line = (
        f"{day.description.capitalize()}, {day.temp_current:.0f}°C maintenant"
        if day.temp_current is not None
        else f"{day.description.capitalize()}"
    )
    details = (
        f"min {day.temp_min:.0f}°C / max {day.temp_max:.0f}°C — "
        f"Précipitations : {day.precipitation_mm:.0f} mm — "
        f"Vent max : {day.wind_kmh_max:.0f} km/h"
    )
    return f"{header}\n{current_line}\n{details}"


def _format_weather_multi(days: Sequence[DailyWeather], place: str, period: str) -> str:
    header = f"🌤 *Météo — {place}* ({period})"
    lines = [
        f"- {_fr_day_label(d.date)} : {d.description}, "
        f"{d.temp_min:.0f}-{d.temp_max:.0f}°C, "
        f"{d.precipitation_mm:.0f} mm, vent {d.wind_kmh_max:.0f} km/h"
        for d in days
    ]
    return f"{header}\n" + "\n".join(lines)


_FR_WEEKDAYS_SHORT: tuple[str, ...] = ("lun", "mar", "mer", "jeu", "ven", "sam", "dim")


def _fr_day_label(d: date) -> str:
    """Ex: 'sam 26/04' — mapping manuel pour ne pas dépendre de la locale système."""
    return f"{_FR_WEEKDAYS_SHORT[d.weekday()]} {d.strftime('%d/%m')}"


def _weather_period_label(when_str: str | None) -> str:
    """Label humain affiché dans l'en-tête météo ; recopie ce que l'utilisateur a dit."""
    if not when_str:
        return "aujourd'hui"
    return when_str.strip()


def _parse_weather_range(when_str: str | None, tz: ZoneInfo) -> tuple[int, int]:
    """Convertit une expression FR en (offset_début, offset_fin) en jours depuis aujourd'hui.

    Défaut (aucune expression) : (0, 0) = aujourd'hui. Sinon, matches explicites
    pour les expressions courantes, fallback dateparser pour le reste.
    """
    if not when_str:
        return 0, 0

    today = datetime.now(tz).date()
    lowered = when_str.strip().lower()

    if "aujourd" in lowered or "ce jour" in lowered or "maintenant" in lowered:
        return 0, 0
    if "après-demain" in lowered or "apres-demain" in lowered:
        return 2, 2
    if "demain" in lowered:
        return 1, 1
    if "weekend" in lowered or "week-end" in lowered:
        wd = today.weekday()  # 0=lundi .. 6=dimanche
        if wd < 5:
            return 5 - wd, 6 - wd
        if wd == 5:
            return 0, 1
        return 0, 0  # dimanche = fin de weekend déjà là
    if "semaine" in lowered:
        return 0, 6

    parsed = dateparser.parse(
        when_str,
        languages=["fr"],
        settings={
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": str(tz),
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if parsed is None:
        return 0, 0
    offset = (parsed.date() - today).days
    if offset < 0:
        offset = 0
    if offset > 15:
        offset = 15
    return offset, offset


def _format_station(rank: int, station: FuelStation) -> str:
    location_parts = [part for part in (station.address, station.postal_code, station.city) if part]
    location = ", ".join(location_parts) if location_parts else "adresse inconnue"
    return f"{rank}. {station.price_eur:.3f} € — {location} ({station.distance_km:.1f} km)"


def _format_km(km: float) -> str:
    """Formate un rayon en km : entier si c'en est un, sinon 1 décimale."""
    if float(km).is_integer():
        return f"{int(km)} km"
    return f"{km:.1f} km"


def _format_freshness(stations: Sequence[FuelStation], tz: ZoneInfo) -> str | None:
    """Retourne une ligne « Prix mis à jour il y a … » basée sur la station la plus fraîche."""
    now = datetime.now(tz)
    ages: list[timedelta] = []
    for s in stations:
        if s.updated_at is None:
            continue
        updated = (
            s.updated_at if s.updated_at.tzinfo is not None else s.updated_at.replace(tzinfo=tz)
        )
        delta = now - updated
        if delta.total_seconds() >= 0:
            ages.append(delta)
    if not ages:
        return None
    freshest = min(ages)
    return f"(Prix mis à jour {_humanize_age(freshest)})"


def _humanize_age(delta: timedelta) -> str:
    """Ex: 'il y a 2h', 'il y a 15 min', 'il y a 3 jours'."""
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        return "à l'instant"
    minutes = total_seconds // 60
    if minutes < 60:
        return f"il y a {minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"il y a {hours}h"
    days = hours // 24
    return f"il y a {days} j"


def _parse_range(range_str: str | None, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Convertit une expression FR de plage en (start, end) timezone-aware.

    Par défaut (range_str absent) : 7 jours à venir. Sinon on utilise dateparser
    pour identifier un repère, et on étend symboliquement 'aujourd'hui', 'demain',
    'cette semaine', 'ce mois', etc.
    """
    now = datetime.now(tz)
    if not range_str:
        return now, now + timedelta(days=7)

    lowered = range_str.strip().lower()
    today = datetime.combine(now.date(), time.min, tzinfo=tz)

    if "aujourd" in lowered:
        return today, today.replace(hour=23, minute=59, second=59)
    if "demain" in lowered:
        start = today + timedelta(days=1)
        return start, start.replace(hour=23, minute=59, second=59)
    if "semaine" in lowered:
        return now, now + timedelta(days=7)
    if "mois" in lowered:
        return now, now + timedelta(days=30)

    parsed = dateparser.parse(
        range_str,
        languages=["fr"],
        settings={
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": str(tz),
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if parsed is None:
        return now, now + timedelta(days=7)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    start = datetime.combine(parsed.date(), time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def _parse_due(due_str: str | None, tz_name: str) -> datetime | None:
    """Parse une expression FR et retourne un datetime aware dans la timezone voulue.

    Sans `TIMEZONE` + `RETURN_AS_TIMEZONE_AWARE`, dateparser renvoie un datetime
    naïf, qu'APScheduler interprète en UTC → décalage en prod (le container est
    souvent en UTC).

    dateparser FR ne reconnaît pas « midi » / « minuit » : on les pré-normalise.
    """
    if not due_str:
        return None
    normalized = _normalize_fr_time_words(due_str)
    parsed = dateparser.parse(
        normalized,
        languages=["fr"],
        settings={
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": tz_name,
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(tz_name))
    return parsed


_FR_TIME_SUBSTITUTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # "après-midi" DOIT être traité avant "midi" pour éviter que "midi" soit
    # substitué à l'intérieur du mot composé (après-12:00).
    (re.compile(r"\bce\s+matin\b", re.IGNORECASE), "aujourd'hui"),
    (re.compile(r"\bce\s+soir\b", re.IGNORECASE), "aujourd'hui"),
    (re.compile(r"\bcet?\s+après-midi\b", re.IGNORECASE), "aujourd'hui"),
    (re.compile(r"\baprès-midi\b", re.IGNORECASE), ""),
    # "midi" / "minuit" après avoir éliminé "après-midi"
    (re.compile(r"\bmidi\b", re.IGNORECASE), "12:00"),
    (re.compile(r"\bminuit\b", re.IGNORECASE), "00:00"),
    # Mots de moment isolés (ex: "demain matin", "lundi soir") : supprimés car
    # l'heure explicite suffit à dateparser.
    (re.compile(r"\bmatin\b", re.IGNORECASE), ""),
    (re.compile(r"\bsoir\b", re.IGNORECASE), ""),
)


def _normalize_fr_time_words(expr: str) -> str:
    """Remplace les mots FR que dateparser ignore par des expressions qu'il gère."""
    for pattern, repl in _FR_TIME_SUBSTITUTIONS:
        expr = pattern.sub(repl, expr)
    return " ".join(expr.split())  # nettoie les espaces doubles laissés par les suppressions
