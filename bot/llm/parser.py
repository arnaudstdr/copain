"""Extraction et validation du bloc <meta> JSON produit par le LLM."""

from __future__ import annotations

import json
import re
from typing import Any, Literal, TypedDict, get_args

META_PATTERN = re.compile(r"<meta>\s*(\{.*?\})\s*</meta>", re.DOTALL)

# Source unique de vérité : le frozenset est dérivé du Literal via get_args().
# Ajouter un nouvel intent/action ne requiert de modifier qu'un seul endroit.
Intent = Literal["answer", "task", "search", "memory", "feed", "event", "fuel", "weather"]
VALID_INTENTS: frozenset[str] = frozenset(get_args(Intent))

FeedAction = Literal["add", "list", "remove", "summarize"]
VALID_FEED_ACTIONS: frozenset[str] = frozenset(get_args(FeedAction))

EventAction = Literal["create", "list"]
VALID_EVENT_ACTIONS: frozenset[str] = frozenset(get_args(EventAction))


class TaskMeta(TypedDict):
    content: str | None
    due_str: str | None


class FeedMeta(TypedDict):
    action: FeedAction | None
    name: str | None
    url: str | None


class EventMeta(TypedDict):
    action: EventAction | None
    title: str | None
    start_str: str | None
    end_str: str | None
    location: str | None
    description: str | None
    range_str: str | None
    calendar_name: str | None


class FuelMeta(TypedDict):
    fuel_type: str | None
    radius_km: float | None
    location: str | None


class WeatherMeta(TypedDict):
    location: str | None
    when: str | None


class Meta(TypedDict):
    intent: Intent
    store_memory: bool
    memory_content: str | None
    task: TaskMeta
    feed: FeedMeta
    event: EventMeta
    fuel: FuelMeta
    weather: WeatherMeta
    search_query: str | None


class MetaParseError(ValueError):
    """Levée si le bloc <meta> est absent, mal formé, ou de schéma invalide."""


def extract_meta(raw: str) -> tuple[str, Meta]:
    """Extrait le bloc <meta>, le parse, et retourne (texte_propre, meta_validée).

    Lève MetaParseError si le bloc est absent, non parsable en JSON, ou si le
    schéma est invalide. L'appelant doit gérer ce cas (réponse par défaut).
    """
    match = META_PATTERN.search(raw)
    if not match:
        raise MetaParseError("Bloc <meta> absent de la réponse du LLM")

    json_str = match.group(1)
    try:
        data: Any = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise MetaParseError(f"JSON du bloc <meta> invalide : {exc.msg}") from exc

    meta = _validate(data)
    clean_text = META_PATTERN.sub("", raw).strip()
    return clean_text, meta


def _validate(data: Any) -> Meta:
    if not isinstance(data, dict):
        raise MetaParseError("Le bloc <meta> doit être un objet JSON")

    intent = data.get("intent")
    if intent not in VALID_INTENTS:
        raise MetaParseError(f"intent invalide : {intent!r}")

    store_memory = data.get("store_memory")
    if not isinstance(store_memory, bool):
        raise MetaParseError("store_memory doit être un booléen")

    memory_content = data.get("memory_content")
    if memory_content is not None and not isinstance(memory_content, str):
        raise MetaParseError("memory_content doit être une chaîne ou null")

    task_raw = data.get("task") or {"content": None, "due_str": None}
    if not isinstance(task_raw, dict):
        raise MetaParseError("task doit être un objet ou null")
    task: TaskMeta = {
        "content": _opt_str(task_raw.get("content"), "task.content"),
        "due_str": _opt_str(task_raw.get("due_str"), "task.due_str"),
    }

    feed_raw = data.get("feed") or {"action": None, "name": None, "url": None}
    if not isinstance(feed_raw, dict):
        raise MetaParseError("feed doit être un objet ou null")
    feed_action = feed_raw.get("action")
    if feed_action is not None and feed_action not in VALID_FEED_ACTIONS:
        raise MetaParseError(f"feed.action invalide : {feed_action!r}")
    feed: FeedMeta = {
        "action": feed_action,
        "name": _opt_str(feed_raw.get("name"), "feed.name"),
        "url": _opt_str(feed_raw.get("url"), "feed.url"),
    }

    event_raw = data.get("event") or {
        "action": None,
        "title": None,
        "start_str": None,
        "end_str": None,
        "location": None,
        "description": None,
        "range_str": None,
        "calendar_name": None,
    }
    if not isinstance(event_raw, dict):
        raise MetaParseError("event doit être un objet ou null")
    event_action = event_raw.get("action")
    if event_action is not None and event_action not in VALID_EVENT_ACTIONS:
        raise MetaParseError(f"event.action invalide : {event_action!r}")
    event: EventMeta = {
        "action": event_action,
        "title": _opt_str(event_raw.get("title"), "event.title"),
        "start_str": _opt_str(event_raw.get("start_str"), "event.start_str"),
        "end_str": _opt_str(event_raw.get("end_str"), "event.end_str"),
        "location": _opt_str(event_raw.get("location"), "event.location"),
        "description": _opt_str(event_raw.get("description"), "event.description"),
        "range_str": _opt_str(event_raw.get("range_str"), "event.range_str"),
        "calendar_name": _opt_str(event_raw.get("calendar_name"), "event.calendar_name"),
    }

    fuel_raw = data.get("fuel") or {
        "fuel_type": None,
        "radius_km": None,
        "location": None,
    }
    if not isinstance(fuel_raw, dict):
        raise MetaParseError("fuel doit être un objet ou null")
    fuel: FuelMeta = {
        "fuel_type": _opt_str(fuel_raw.get("fuel_type"), "fuel.fuel_type"),
        "radius_km": _opt_float(fuel_raw.get("radius_km"), "fuel.radius_km"),
        "location": _opt_str(fuel_raw.get("location"), "fuel.location"),
    }

    weather_raw = data.get("weather") or {"location": None, "when": None}
    if not isinstance(weather_raw, dict):
        raise MetaParseError("weather doit être un objet ou null")
    weather: WeatherMeta = {
        "location": _opt_str(weather_raw.get("location"), "weather.location"),
        "when": _opt_str(weather_raw.get("when"), "weather.when"),
    }

    search_query = _opt_str(data.get("search_query"), "search_query")

    return Meta(
        intent=intent,
        store_memory=store_memory,
        memory_content=memory_content,
        task=task,
        feed=feed,
        event=event,
        fuel=fuel,
        weather=weather,
        search_query=search_query,
    )


def _opt_str(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MetaParseError(f"{field} doit être une chaîne ou null")
    return value


def _opt_float(value: Any, field: str) -> float | None:
    """Accepte int, float, ou str numérique. `True`/`False` rejetés (bool is int)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise MetaParseError(f"{field} doit être un nombre ou null")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError as exc:
            raise MetaParseError(f"{field} doit être un nombre ou null") from exc
    raise MetaParseError(f"{field} doit être un nombre ou null")
