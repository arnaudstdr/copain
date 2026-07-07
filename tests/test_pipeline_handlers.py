"""Tests isolés des handlers d'intents (`bot/pipeline/handlers.py`).

Contrairement à `test_pipeline_process.py` (qui mocke tout le LLM et vérifie
l'orchestration), on appelle ici les handlers directement avec une `Meta`
fabriquée et un `BotDeps` mocké, pour exercer la logique métier et surtout
les chemins d'erreur de chaque sous-système (geocode, calendrier, météo,
carburant) — qui n'étaient couverts qu'implicitement jusqu'ici.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from bot.calendar.client import ICloudCalendarError
from bot.calendar.models import CalendarEvent
from bot.fuel.client import FuelError
from bot.fuel.geocoding import NominatimError
from bot.fuel.models import FuelStation, GeoPoint
from bot.fuel.overpass import BrandPoint, OverpassError
from bot.pipeline.handlers import (
    FALLBACK_TEXT,
    _fr_day_label,
    _humanize_age,
    _weather_period_label,
    format_freshness,
    format_km,
    format_station,
    format_weather_multi,
    format_weather_single,
    handle_event,
    handle_feed,
    handle_fuel,
    handle_weather,
    run_intent_handler,
)
from bot.rss.manager import FeedAlreadyExists
from bot.weather.client import DailyWeather, WeatherError
from tests.conftest import make_meta

if TYPE_CHECKING:
    from bot.pipeline.core import BotDeps

PARIS = ZoneInfo("Europe/Paris")


def _feed(name: str = "Le Monde", *, enabled: bool = True) -> MagicMock:
    feed = MagicMock()
    feed.name = name
    feed.url = f"https://example.com/{name}.xml"
    feed.category = "general"
    feed.enabled = enabled
    return feed


def _station(price: float = 1.799, distance: float = 2.3) -> FuelStation:
    return FuelStation(
        id="42",
        address="1 rue de la Gare",
        city="Sélestat",
        postal_code="67600",
        lat=48.26,
        lon=7.45,
        distance_km=distance,
        fuel_type="gazole",
        price_eur=price,
        updated_at=None,
    )


def _daily(d: date, *, temp_current: float | None = None) -> DailyWeather:
    return DailyWeather(
        city="Sélestat",
        date=d,
        temp_min=8.0,
        temp_max=17.0,
        precipitation_mm=1.0,
        wind_kmh_max=20.0,
        description="ciel dégagé",
        temp_current=temp_current,
    )


# --- run_intent_handler (dispatch) -------------------------------------------


async def test_dispatch_answer_returns_none(bot_deps: BotDeps) -> None:
    meta = make_meta(intent="answer")
    assert await run_intent_handler("salut", meta, bot_deps, intro="ok") is None


async def test_dispatch_feed_without_action_returns_none(bot_deps: BotDeps) -> None:
    meta = make_meta(intent="feed")  # feed.action reste None
    assert await run_intent_handler("flux", meta, bot_deps, intro="ok") is None


async def test_dispatch_event_without_action_returns_none(bot_deps: BotDeps) -> None:
    meta = make_meta(intent="event")
    assert await run_intent_handler("agenda", meta, bot_deps, intro="ok") is None


async def test_dispatch_fuel_without_type_returns_none(bot_deps: BotDeps) -> None:
    meta = make_meta(intent="fuel")  # fuel.fuel_type reste None
    assert await run_intent_handler("essence", meta, bot_deps, intro="ok") is None


async def test_dispatch_weather_always_handled(bot_deps: BotDeps) -> None:
    bot_deps.weather.get_forecast = AsyncMock(return_value=[_daily(date(2026, 6, 3))])
    meta = make_meta(intent="weather")
    result = await run_intent_handler("météo", meta, bot_deps, intro="ok")
    assert result is not None


# --- handle_feed -------------------------------------------------------------


async def test_feed_add_success(bot_deps: BotDeps) -> None:
    bot_deps.rss.add = AsyncMock(return_value=_feed("Le Monde"))
    meta = make_meta(
        intent="feed",
        feed={"action": "add", "name": "Le Monde", "url": "https://x/rss"},
    )
    out = await handle_feed("ajoute", meta, bot_deps, intro="")
    assert "Flux ajouté" in out and "Le Monde" in out


async def test_feed_add_missing_name_or_url(bot_deps: BotDeps) -> None:
    meta = make_meta(intent="feed", feed={"action": "add", "name": "Le Monde"})
    out = await handle_feed("ajoute", meta, bot_deps, intro="")
    assert "nom et une URL" in out
    bot_deps.rss.add.assert_not_called()


async def test_feed_add_already_exists(bot_deps: BotDeps) -> None:
    bot_deps.rss.add = AsyncMock(side_effect=FeedAlreadyExists())
    meta = make_meta(
        intent="feed", feed={"action": "add", "name": "Le Monde", "url": "https://x/rss"}
    )
    out = await handle_feed("ajoute", meta, bot_deps, intro="")
    assert "existe déjà" in out


async def test_feed_list_empty(bot_deps: BotDeps) -> None:
    bot_deps.rss.list = AsyncMock(return_value=[])
    meta = make_meta(intent="feed", feed={"action": "list"})
    out = await handle_feed("liste", meta, bot_deps, intro="")
    assert out == "Aucun flux enregistré."


async def test_feed_list_with_feeds(bot_deps: BotDeps) -> None:
    bot_deps.rss.list = AsyncMock(return_value=[_feed("A"), _feed("B", enabled=False)])
    meta = make_meta(intent="feed", feed={"action": "list"})
    out = await handle_feed("liste", meta, bot_deps, intro="")
    assert "A" in out and "B" in out and "✓" in out and "✗" in out


async def test_feed_remove_without_name(bot_deps: BotDeps) -> None:
    meta = make_meta(intent="feed", feed={"action": "remove"})
    out = await handle_feed("supprime", meta, bot_deps, intro="")
    assert "quel flux" in out


async def test_feed_remove_not_found(bot_deps: BotDeps) -> None:
    bot_deps.rss.remove = AsyncMock(return_value=False)
    meta = make_meta(intent="feed", feed={"action": "remove", "name": "X"})
    out = await handle_feed("supprime", meta, bot_deps, intro="")
    assert "Aucun flux trouvé" in out


async def test_feed_summarize_no_active_feeds(bot_deps: BotDeps) -> None:
    bot_deps.rss.list = AsyncMock(return_value=[])
    meta = make_meta(intent="feed", feed={"action": "summarize"})
    out = await handle_feed("résume", meta, bot_deps, intro="")
    assert "Aucun flux actif" in out


async def test_feed_summarize_named_feed_not_found(bot_deps: BotDeps) -> None:
    bot_deps.rss.get = AsyncMock(return_value=None)
    meta = make_meta(intent="feed", feed={"action": "summarize", "name": "Introuvable"})
    out = await handle_feed("résume", meta, bot_deps, intro="")
    assert "Aucun flux trouvé" in out


async def test_feed_summarize_no_items(bot_deps: BotDeps) -> None:
    bot_deps.rss.list = AsyncMock(return_value=[_feed("A")])
    bot_deps.rss_fetcher.fetch_many = AsyncMock(return_value=[])
    meta = make_meta(intent="feed", feed={"action": "summarize"})
    out = await handle_feed("résume", meta, bot_deps, intro="")
    assert "Aucun article" in out


async def test_feed_summarize_success_replaces_fallback_intro(bot_deps: BotDeps) -> None:
    bot_deps.rss.list = AsyncMock(return_value=[_feed("A")])
    item = MagicMock(feed_name="A", title="Titre", url="https://x", summary="résumé")
    bot_deps.rss_fetcher.fetch_many = AsyncMock(return_value=[item])
    bot_deps.llm.chat = AsyncMock(return_value="Résumé final.")
    meta = make_meta(intent="feed", feed={"action": "summarize"})
    # intro = FALLBACK_TEXT → le résumé remplace entièrement.
    out = await handle_feed("résume", meta, bot_deps, intro=FALLBACK_TEXT)
    assert out == "Résumé final."


async def test_feed_summarize_success_keeps_real_intro(bot_deps: BotDeps) -> None:
    bot_deps.rss.list = AsyncMock(return_value=[_feed("A")])
    item = MagicMock(feed_name="A", title="Titre", url="https://x", summary="résumé")
    bot_deps.rss_fetcher.fetch_many = AsyncMock(return_value=[item])
    bot_deps.llm.chat = AsyncMock(return_value="Résumé final.")
    meta = make_meta(intent="feed", feed={"action": "summarize"})
    out = await handle_feed("résume", meta, bot_deps, intro="Voici tes actus :")
    assert out.startswith("Voici tes actus :") and "Résumé final." in out


# --- handle_event ------------------------------------------------------------


async def test_event_calendar_not_connected(bot_deps: BotDeps) -> None:
    bot_deps.calendar.is_connected = False
    meta = make_meta(
        intent="event", event={"action": "create", "title": "RDV", "start_str": "demain 9h"}
    )
    out = await handle_event(meta, bot_deps, intro="")
    assert "n'est pas disponible" in out


async def test_event_create_missing_title_or_start(bot_deps: BotDeps) -> None:
    meta = make_meta(intent="event", event={"action": "create", "title": "RDV"})
    out = await handle_event(meta, bot_deps, intro="")
    assert "titre et une heure" in out


async def test_event_create_unparseable_start(bot_deps: BotDeps) -> None:
    meta = make_meta(
        intent="event",
        event={"action": "create", "title": "RDV", "start_str": "n'importe quoi xyz"},
    )
    out = await handle_event(meta, bot_deps, intro="")
    assert "Impossible d'interpréter" in out


async def test_event_create_success(bot_deps: BotDeps) -> None:
    start = datetime(2026, 6, 4, 9, 0, tzinfo=PARIS)
    created = CalendarEvent(
        uid="u1",
        title="RDV dentiste",
        start=start,
        end=start + timedelta(hours=1),
        location=None,
        description=None,
        calendar_name="Perso",
    )
    bot_deps.calendar.list_all_between = AsyncMock(return_value=[])
    bot_deps.calendar.create_event = AsyncMock(return_value=created)
    meta = make_meta(
        intent="event",
        event={"action": "create", "title": "RDV dentiste", "start_str": "demain 9h"},
    )
    out = await handle_event(meta, bot_deps, intro="")
    assert "Ajouté au calendrier" in out and "RDV dentiste" in out
    assert "⚠️" not in out


async def test_event_create_detects_overlap(bot_deps: BotDeps) -> None:
    start = datetime(2026, 6, 4, 9, 0, tzinfo=PARIS)
    existing = CalendarEvent(
        uid="u0",
        title="Réunion",
        start=start,
        end=start + timedelta(hours=1),
        location=None,
        description=None,
        calendar_name="Pro",
    )
    created = CalendarEvent(
        uid="u1",
        title="RDV",
        start=start,
        end=start + timedelta(hours=1),
        location=None,
        description=None,
        calendar_name="Perso",
    )
    bot_deps.calendar.list_all_between = AsyncMock(return_value=[existing])
    bot_deps.calendar.create_event = AsyncMock(return_value=created)
    meta = make_meta(
        intent="event", event={"action": "create", "title": "RDV", "start_str": "demain 9h"}
    )
    out = await handle_event(meta, bot_deps, intro="")
    assert "⚠️" in out and "Réunion" in out


async def test_event_create_overlap_check_failure_is_swallowed(bot_deps: BotDeps) -> None:
    # Le check de chevauchement échoue, mais la création doit aboutir quand même.
    start = datetime(2026, 6, 4, 9, 0, tzinfo=PARIS)
    created = CalendarEvent(
        uid="u1",
        title="RDV",
        start=start,
        end=start + timedelta(hours=1),
        location=None,
        description=None,
        calendar_name="Perso",
    )
    bot_deps.calendar.list_all_between = AsyncMock(side_effect=ICloudCalendarError("boom"))
    bot_deps.calendar.create_event = AsyncMock(return_value=created)
    meta = make_meta(
        intent="event", event={"action": "create", "title": "RDV", "start_str": "demain 9h"}
    )
    out = await handle_event(meta, bot_deps, intro="")
    assert "Ajouté au calendrier" in out


async def test_event_create_failure(bot_deps: BotDeps) -> None:
    bot_deps.calendar.list_all_between = AsyncMock(return_value=[])
    bot_deps.calendar.create_event = AsyncMock(side_effect=ICloudCalendarError("auth"))
    meta = make_meta(
        intent="event", event={"action": "create", "title": "RDV", "start_str": "demain 9h"}
    )
    out = await handle_event(meta, bot_deps, intro="")
    assert "impossible de créer l'évènement" in out


async def test_event_list_empty(bot_deps: BotDeps) -> None:
    bot_deps.calendar.list_all_between = AsyncMock(return_value=[])
    meta = make_meta(intent="event", event={"action": "list", "range_str": "cette semaine"})
    out = await handle_event(meta, bot_deps, intro="")
    assert "Aucun évènement" in out and "cette semaine" in out


async def test_event_list_with_events(bot_deps: BotDeps) -> None:
    start = datetime(2026, 6, 4, 9, 0, tzinfo=PARIS)
    ev = CalendarEvent(
        uid="u1",
        title="Réunion",
        start=start,
        end=start + timedelta(hours=1),
        location="Bureau",
        description=None,
        calendar_name="Pro",
    )
    bot_deps.calendar.list_all_between = AsyncMock(return_value=[ev])
    meta = make_meta(intent="event", event={"action": "list", "range_str": "demain"})
    out = await handle_event(meta, bot_deps, intro="")
    assert "Réunion" in out and "Bureau" in out


async def test_event_list_failure(bot_deps: BotDeps) -> None:
    bot_deps.calendar.list_all_between = AsyncMock(side_effect=ICloudCalendarError("down"))
    meta = make_meta(intent="event", event={"action": "list", "range_str": "demain"})
    out = await handle_event(meta, bot_deps, intro="")
    assert "lecture du calendrier impossible" in out


# --- handle_fuel -------------------------------------------------------------


async def test_fuel_unknown_type(bot_deps: BotDeps) -> None:
    meta = make_meta(intent="fuel", fuel={"fuel_type": "charbon"})
    out = await handle_fuel(meta, bot_deps, intro="")
    assert "ne reconnais pas le carburant" in out


async def test_fuel_geocode_error(bot_deps: BotDeps) -> None:
    bot_deps.geocoder.geocode_fr = AsyncMock(side_effect=NominatimError("ko"))
    meta = make_meta(intent="fuel", fuel={"fuel_type": "gazole", "location": "Lyon"})
    out = await handle_fuel(meta, bot_deps, intro="")
    assert "impossible de localiser" in out


async def test_fuel_geocode_not_found(bot_deps: BotDeps) -> None:
    bot_deps.geocoder.geocode_fr = AsyncMock(return_value=None)
    meta = make_meta(intent="fuel", fuel={"fuel_type": "gazole", "location": "Zzz"})
    out = await handle_fuel(meta, bot_deps, intro="")
    assert "Je n'ai pas trouvé" in out and "Zzz" in out


async def test_fuel_fetch_error(bot_deps: BotDeps) -> None:
    bot_deps.fuel.find_cheapest = AsyncMock(side_effect=FuelError("api down"))
    meta = make_meta(intent="fuel", fuel={"fuel_type": "diesel"})  # synonyme → gazole
    out = await handle_fuel(meta, bot_deps, intro="")
    assert "impossible de récupérer les prix" in out


async def test_fuel_no_stations(bot_deps: BotDeps) -> None:
    bot_deps.fuel.find_cheapest = AsyncMock(return_value=[])
    meta = make_meta(intent="fuel", fuel={"fuel_type": "gazole"})
    out = await handle_fuel(meta, bot_deps, intro="")
    assert "Aucune station" in out and "Sélestat" in out


async def test_fuel_success_uses_home_when_no_location(bot_deps: BotDeps) -> None:
    bot_deps.fuel.find_cheapest = AsyncMock(return_value=[_station(1.799)])
    meta = make_meta(intent="fuel", fuel={"fuel_type": "gazole"})
    out = await handle_fuel(meta, bot_deps, intro="")
    assert "Gazole" in out and "Sélestat" in out and "1.799" in out
    # find_cheapest interrogé autour du domicile.
    _, kwargs = bot_deps.fuel.find_cheapest.call_args
    assert kwargs["center"] == GeoPoint(lat=48.26, lon=7.45)


async def test_fuel_success_uses_geocoded_location(bot_deps: BotDeps) -> None:
    bot_deps.geocoder.geocode_fr = AsyncMock(return_value=GeoPoint(lat=45.7, lon=4.8))
    bot_deps.fuel.find_cheapest = AsyncMock(return_value=[_station()])
    meta = make_meta(intent="fuel", fuel={"fuel_type": "gazole", "location": "Lyon"})
    out = await handle_fuel(meta, bot_deps, intro="")
    assert "Lyon" in out
    _, kwargs = bot_deps.fuel.find_cheapest.call_args
    assert kwargs["center"] == GeoPoint(lat=45.7, lon=4.8)


async def test_fuel_enriches_station_with_brand(bot_deps: BotDeps) -> None:
    bot_deps.fuel.find_cheapest = AsyncMock(return_value=[_station(1.799)])
    # Point OSM quasi confondu avec la station (_station : lat=48.26, lon=7.45).
    bot_deps.overpass.find_fuel_stations = AsyncMock(
        return_value=[BrandPoint(lat=48.2601, lon=7.4501, brand="TotalEnergies")]
    )
    meta = make_meta(intent="fuel", fuel={"fuel_type": "gazole"})
    out = await handle_fuel(meta, bot_deps, intro="")
    assert "TotalEnergies" in out and "1.799" in out


async def test_fuel_enrichment_failsoft_on_overpass_error(bot_deps: BotDeps) -> None:
    bot_deps.fuel.find_cheapest = AsyncMock(return_value=[_station(1.799)])
    bot_deps.overpass.find_fuel_stations = AsyncMock(side_effect=OverpassError("down"))
    meta = make_meta(intent="fuel", fuel={"fuel_type": "gazole"})
    out = await handle_fuel(meta, bot_deps, intro="")
    # L'enseigne manque mais la réponse carburant reste complète.
    assert "1.799" in out and "Sélestat" in out


# --- handle_weather ----------------------------------------------------------


async def test_weather_geocode_error(bot_deps: BotDeps) -> None:
    bot_deps.geocoder.geocode_fr = AsyncMock(side_effect=NominatimError("ko"))
    meta = make_meta(intent="weather", weather={"location": "Lyon"})
    out = await handle_weather(meta, bot_deps, intro="")
    assert "impossible de localiser" in out


async def test_weather_geocode_not_found(bot_deps: BotDeps) -> None:
    bot_deps.geocoder.geocode_fr = AsyncMock(return_value=None)
    meta = make_meta(intent="weather", weather={"location": "Zzz"})
    out = await handle_weather(meta, bot_deps, intro="")
    assert "Je n'ai pas trouvé" in out


async def test_weather_fetch_error(bot_deps: BotDeps) -> None:
    bot_deps.weather.get_forecast = AsyncMock(side_effect=WeatherError("api"))
    meta = make_meta(intent="weather")
    out = await handle_weather(meta, bot_deps, intro="")
    assert "impossible de récupérer la météo" in out


async def test_weather_empty_forecast(bot_deps: BotDeps) -> None:
    bot_deps.weather.get_forecast = AsyncMock(return_value=[])
    meta = make_meta(intent="weather")
    out = await handle_weather(meta, bot_deps, intro="")
    assert "Aucune prévision" in out


async def test_weather_single_day_today(bot_deps: BotDeps) -> None:
    bot_deps.weather.get_forecast = AsyncMock(
        return_value=[_daily(date(2026, 6, 3), temp_current=15.0)]
    )
    meta = make_meta(intent="weather")  # when=None → aujourd'hui (offset 0..0)
    out = await handle_weather(meta, bot_deps, intro="")
    assert "Météo" in out and "Sélestat" in out and "maintenant" in out


async def test_weather_multi_day_range(bot_deps: BotDeps) -> None:
    forecast = [_daily(date(2026, 6, 3) + timedelta(days=i)) for i in range(7)]
    bot_deps.weather.get_forecast = AsyncMock(return_value=forecast)
    meta = make_meta(intent="weather", weather={"when": "cette semaine"})
    out = await handle_weather(meta, bot_deps, intro="")
    assert "Météo" in out
    # Plusieurs jours listés → format multi (tirets).
    assert out.count("- ") >= 2


# --- Formateurs purs ---------------------------------------------------------


def test_format_km_integer_vs_decimal() -> None:
    assert format_km(10.0) == "10 km"
    assert format_km(7.5) == "7.5 km"


def test_format_station() -> None:
    line = format_station(1, _station(price=1.812, distance=3.4))
    assert line.startswith("1. ") and "1.812 €" in line and "3.4 km" in line


def test_format_station_with_brand() -> None:
    from dataclasses import replace

    line = format_station(1, replace(_station(price=1.812), brand="Intermarché"))
    assert line.startswith("1. Intermarché — ") and "1.812 €" in line


def test_humanize_age_all_branches() -> None:
    assert _humanize_age(timedelta(seconds=10)) == "à l'instant"
    assert _humanize_age(timedelta(minutes=15)) == "il y a 15 min"
    assert _humanize_age(timedelta(hours=2)) == "il y a 2h"
    assert _humanize_age(timedelta(days=3)) == "il y a 3 j"


def test_format_freshness_none_when_no_updated_at() -> None:
    assert format_freshness([_station()], PARIS) is None


def test_format_freshness_ignores_future_dates() -> None:
    now = datetime.now(PARIS)
    future = FuelStation(
        id="1",
        address="a",
        city="c",
        postal_code="0",
        lat=0,
        lon=0,
        distance_km=1.0,
        fuel_type="gazole",
        price_eur=1.5,
        updated_at=now + timedelta(hours=1),
    )
    assert format_freshness([future], PARIS) is None


def test_format_freshness_picks_freshest() -> None:
    now = datetime.now(PARIS)
    old = FuelStation(
        id="1",
        address="a",
        city="c",
        postal_code="0",
        lat=0,
        lon=0,
        distance_km=1.0,
        fuel_type="gazole",
        price_eur=1.5,
        updated_at=now - timedelta(days=2),
    )
    recent = FuelStation(
        id="2",
        address="b",
        city="c",
        postal_code="0",
        lat=0,
        lon=0,
        distance_km=1.0,
        fuel_type="gazole",
        price_eur=1.6,
        updated_at=now - timedelta(minutes=30),
    )
    out = format_freshness([old, recent], PARIS)
    assert out is not None and "30 min" in out


def test_fr_day_label() -> None:
    # 2026-06-04 est un jeudi.
    assert _fr_day_label(date(2026, 6, 4)) == "jeu 04/06"


def test_weather_period_label() -> None:
    assert _weather_period_label(None) == "aujourd'hui"
    assert _weather_period_label("  demain  ") == "demain"


def test_format_weather_single_with_and_without_current() -> None:
    with_current = format_weather_single(
        _daily(date(2026, 6, 3), temp_current=15.0), "Paris", "auj"
    )
    assert "maintenant" in with_current and "Paris" in with_current
    without = format_weather_single(_daily(date(2026, 6, 3)), "Paris", "auj")
    assert "maintenant" not in without


def test_format_weather_multi() -> None:
    days = [_daily(date(2026, 6, 3) + timedelta(days=i)) for i in range(3)]
    out = format_weather_multi(days, "Paris", "cette semaine")
    assert out.count("- ") == 3 and "Paris" in out
