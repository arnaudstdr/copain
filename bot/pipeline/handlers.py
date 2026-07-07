"""Handlers d'intents : dispatch + handlers feed/event/fuel/weather + formateurs.

Chaque handler produit le texte final renvoyé à l'utilisateur quand son
intent est routé par le bloc `<meta>` (le texte du LLM devient alors une
simple intro, conservée ou remplacée selon les cas). Le cas `search` n'est
PAS géré ici : il relance le LLM et chaque orchestrateur (stream /
non-stream) le traite à sa façon.

`BotDeps` n'est importé que sous TYPE_CHECKING : `core` importe ce module
au runtime, jamais l'inverse (DAG d'imports du package).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from bot.calendar.client import ICloudCalendarError
from bot.fuel.client import FuelError
from bot.fuel.geocoding import NominatimError
from bot.fuel.models import FUEL_LABELS, GeoPoint, normalize_fuel_type
from bot.fuel.overpass import OverpassError, nearest_brand
from bot.logging_conf import get_logger
from bot.pipeline.dates import parse_due, parse_range, parse_weather_range
from bot.rss.manager import FeedAlreadyExists
from bot.weather.client import WeatherError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bot.calendar.models import CalendarEvent
    from bot.fuel.models import FuelStation
    from bot.llm.parser import Meta
    from bot.pipeline.core import BotDeps
    from bot.rss.fetcher import FeedItem
    from bot.rss.models import Feed
    from bot.weather.client import DailyWeather

log = get_logger(__name__)

# Texte de repli quand le bloc <meta> est absent / invalide. Vit ici (et pas
# dans core) car les handlers le comparent à l'intro au runtime — l'inverse
# créerait un import circulaire core ↔ handlers.
FALLBACK_TEXT = (
    "J'ai eu un souci pour interpréter la réponse, mais je suis là. Redis-moi ça autrement ?"
)


async def run_intent_handler(
    user_text: str,
    meta: Meta,
    deps: BotDeps,
    intro: str,
) -> str | None:
    """Exécute le handler Python de l'intent et retourne le texte de remplacement.

    Retourne None quand aucun handler ne s'applique (le texte du LLM reste tel
    quel). Le cas `search` n'est PAS géré ici : il relance le LLM et chaque
    chemin (stream / non-stream) le traite à sa façon.
    """
    if meta["intent"] == "feed" and meta["feed"]["action"]:
        return await handle_feed(user_text, meta, deps, intro=intro)
    if meta["intent"] == "event" and meta["event"]["action"]:
        return await handle_event(meta, deps, intro=intro)
    if meta["intent"] == "fuel" and meta["fuel"]["fuel_type"]:
        return await handle_fuel(meta, deps, intro=intro)
    if meta["intent"] == "weather":
        return await handle_weather(meta, deps, intro=intro)
    return None


async def handle_feed(user_text: str, meta: Meta, deps: BotDeps, intro: str) -> str:
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
        ],
        cacheable=True,
    )


async def handle_event(meta: Meta, deps: BotDeps, intro: str) -> str:
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
        start = parse_due(start_str, tz_name)
        if start is None:
            return f"Impossible d'interpréter « {start_str} » comme une date."
        end = parse_due(meta["event"]["end_str"], tz_name)
        if end is None:
            end = start + timedelta(hours=1)
        log.info(
            "event_times_parsed",
            start_str=start_str,
            end_str=meta["event"]["end_str"],
            start=start.isoformat(),
            end=end.isoformat(),
        )

        # Détection de chevauchement avant création (warn-only : on crée
        # quand même si conflit pour respecter l'intention utilisateur).
        # Une erreur sur ce check ne doit pas bloquer la création — on
        # poursuit silencieusement avec un log.
        overlap: list[CalendarEvent] = []
        try:
            overlap = await deps.calendar.list_all_between(start, end)
        except ICloudCalendarError:
            log.warning("overlap_check_failed", title=title)

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
        if overlap:
            others = ", ".join(
                f"{e.title} ({e.start.strftime('%H:%M')}-{e.end.strftime('%H:%M')})"
                for e in overlap
            )
            log.info("event_overlap_detected", title=title, count=len(overlap))
            confirm += f"\n⚠️ Chevauche : {others}"
        return confirm if intro.strip() in ("", FALLBACK_TEXT) else f"{intro}\n{confirm}"

    if action == "list":
        tz = ZoneInfo(deps.settings.timezone)
        range_str = meta["event"]["range_str"]
        start, end = parse_range(range_str, tz)
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


@dataclass(frozen=True, slots=True)
class ResolvedLocation:
    """Lieu résolu pour les handlers géolocalisés (fuel / weather)."""

    center: GeoPoint
    label: str


async def _resolve_location(location_query: str | None, deps: BotDeps) -> ResolvedLocation | str:
    """Résout un lieu : géocode `location_query`, ou retombe sur le domicile.

    Partagé par `handle_fuel` et `handle_weather`. Retourne une
    `ResolvedLocation` en cas de succès, ou un message d'erreur FR (à
    renvoyer tel quel à l'utilisateur) si le géocodage échoue ou ne trouve
    rien. Sans `location_query`, retombe sur `HOME_*` sans appel réseau.
    """
    if not location_query:
        return ResolvedLocation(
            center=GeoPoint(lat=deps.settings.home_lat, lon=deps.settings.home_lon),
            label=deps.settings.home_city,
        )
    try:
        geocoded = await deps.geocoder.geocode_fr(location_query)
    except NominatimError:
        log.exception("geocode_failed", location=location_query)
        return "Désolé, impossible de localiser ce lieu pour l'instant."
    if geocoded is None:
        return f"Je n'ai pas trouvé « {location_query} » sur la carte."
    return ResolvedLocation(center=geocoded, label=location_query)


async def handle_fuel(meta: Meta, deps: BotDeps, intro: str) -> str:
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

    resolved = await _resolve_location(location_query, deps)
    if isinstance(resolved, str):
        return resolved
    center, place_label = resolved.center, resolved.label

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
            f"dans un rayon de {format_km(radius_km)} autour de {place_label}."
        )

    stations = await _enrich_with_brands(stations, center, radius_km, deps)

    tz = ZoneInfo(deps.settings.timezone)
    header = (
        f"⛽ Top {len(stations)} {FUEL_LABELS[fuel_type]} "
        f"(rayon {format_km(radius_km)} autour de {place_label})"
    )
    lines = [format_station(i, s) for i, s in enumerate(stations, start=1)]
    freshness = format_freshness(stations, tz)
    body = f"{header}\n" + "\n".join(lines)
    return body + (f"\n{freshness}" if freshness else "")


async def handle_weather(meta: Meta, deps: BotDeps, intro: str) -> str:
    location_query = meta["weather"]["location"]
    when_str = meta["weather"]["when"]
    log.info("weather_action", location=location_query, when=when_str)

    resolved = await _resolve_location(location_query, deps)
    if isinstance(resolved, str):
        return resolved
    lat, lon, label = resolved.center.lat, resolved.center.lon, resolved.label

    tz = ZoneInfo(deps.settings.timezone)
    start_offset, end_offset = parse_weather_range(when_str, tz)
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
        return format_weather_single(selected[0], label, period_label)
    return format_weather_multi(selected, label, period_label)


def format_weather_single(day: DailyWeather, place: str, period: str) -> str:
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


def format_weather_multi(days: Sequence[DailyWeather], place: str, period: str) -> str:
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


async def _enrich_with_brands(
    stations: Sequence[FuelStation],
    center: GeoPoint,
    radius_km: float,
    deps: BotDeps,
) -> list[FuelStation]:
    """Attache l'enseigne OSM à chaque station (fail-soft).

    Une seule requête Overpass couvre tout le rayon, puis chaque station est
    appariée au point OSM le plus proche. Toute erreur (Overpass indispo,
    payload inattendu) laisse les stations inchangées : l'enseigne est un
    bonus, jamais un point de rupture de la réponse carburant.
    """
    try:
        brand_points = await deps.overpass.find_fuel_stations(center, radius_km)
    except OverpassError:
        log.warning("fuel_brand_enrichment_failed")
        return list(stations)
    if not brand_points:
        return list(stations)

    enriched: list[FuelStation] = []
    for station in stations:
        brand = nearest_brand(station.lat, station.lon, brand_points)
        enriched.append(replace(station, brand=brand) if brand else station)
    return enriched


def format_station(rank: int, station: FuelStation) -> str:
    location_parts = [part for part in (station.address, station.postal_code, station.city) if part]
    location = ", ".join(location_parts) if location_parts else "adresse inconnue"
    brand = f"{station.brand} — " if station.brand else ""
    return f"{rank}. {brand}{station.price_eur:.3f} € — {location} ({station.distance_km:.1f} km)"


def format_km(km: float) -> str:
    """Formate un rayon en km : entier si c'en est un, sinon 1 décimale."""
    if float(km).is_integer():
        return f"{int(km)} km"
    return f"{km:.1f} km"


def format_freshness(stations: Sequence[FuelStation], tz: ZoneInfo) -> str | None:
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
