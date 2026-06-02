"""Extraction et validation du bloc <meta> JSON produit par le LLM."""

from __future__ import annotations

import json
import re
from typing import Any, Literal, TypedDict, get_args

META_PATTERN = re.compile(r"<meta>\s*(\{.*?\})\s*</meta>", re.DOTALL)

# Source unique de vérité : le frozenset est dérivé du Literal via get_args().
# Ajouter un nouvel intent/action ne requiert de modifier qu'un seul endroit.
Intent = Literal[
    "answer",
    "task",
    "search",
    "memory",
    "feed",
    "event",
    "fuel",
    "weather",
    "depot",
    "expense",
]
VALID_INTENTS: frozenset[str] = frozenset(get_args(Intent))

FeedAction = Literal["add", "list", "remove", "summarize"]
VALID_FEED_ACTIONS: frozenset[str] = frozenset(get_args(FeedAction))

EventAction = Literal["create", "list"]
VALID_EVENT_ACTIONS: frozenset[str] = frozenset(get_args(EventAction))

DepotKind = Literal["worry", "idea", "note"]
VALID_DEPOT_KINDS: frozenset[str] = frozenset(get_args(DepotKind))

ExpenseAction = Literal["spend", "income", "tick_recurring"]
VALID_EXPENSE_ACTIONS: frozenset[str] = frozenset(get_args(ExpenseAction))


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


class DepotMeta(TypedDict):
    content: str | None
    kind: DepotKind | None


class ExpenseMeta(TypedDict):
    action: ExpenseAction | None
    amount: float | None  # euros (le pipeline convertit en cents)
    label: str | None
    category: str | None  # libre, uniquement pour action=spend
    recurring_key: str | None  # uniquement pour action=tick_recurring
    when: str | None  # expression FR ("hier"), null = aujourd'hui
    shared: bool  # True si payé sur un compte joint / hors gestion perso
    starts_cycle: bool  # True quand action=income marque la réception du salaire


class Meta(TypedDict):
    intent: Intent
    store_memory: bool
    memory_content: str | None
    task: TaskMeta
    feed: FeedMeta
    event: EventMeta
    fuel: FuelMeta
    weather: WeatherMeta
    depot: DepotMeta
    expense: ExpenseMeta
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


class MetaStreamFilter:
    """Filtre incrémental du bloc `<meta>` pour le streaming.

    Ollama livre la réponse par chunks ; le marqueur `<meta>` peut être coupé
    entre deux chunks (`"...<me"` puis `"ta>{...}"`). `feed(chunk)` retourne le
    texte sûr à émettre immédiatement : tout sauf un suffixe qui pourrait être
    le début du marqueur, ainsi que le whitespace qui le précède (pour coller
    au `.strip()` d'`extract_meta`). Dès que le marqueur complet est détecté,
    plus rien n'est émis.

    `raw` accumule la réponse brute complète (bloc <meta> inclus) : c'est elle
    qu'on passe à `extract_meta` une fois le stream terminé pour récupérer la
    `Meta` validée.
    """

    _MARKER = "<meta>"

    def __init__(self) -> None:
        self._raw_parts: list[str] = []
        self._pending = ""
        self._meta_started = False

    @property
    def raw(self) -> str:
        """Réponse brute complète accumulée (pour `extract_meta` en fin de stream)."""
        return "".join(self._raw_parts)

    @property
    def meta_started(self) -> bool:
        """True dès que le marqueur `<meta>` complet a été vu."""
        return self._meta_started

    def feed(self, chunk: str) -> str:
        """Absorbe un chunk et retourne la portion de texte visible à émettre."""
        self._raw_parts.append(chunk)
        if self._meta_started:
            return ""
        self._pending += chunk
        idx = self._pending.find(self._MARKER)
        if idx != -1:
            self._meta_started = True
            out = self._pending[:idx].rstrip()
            self._pending = ""
            return out
        safe_len = len(self._pending) - self._holdback_len(self._pending)
        out = self._pending[:safe_len]
        self._pending = self._pending[safe_len:]
        return out

    def flush(self) -> str:
        """Vide le buffer en fin de stream (cas : pas de bloc <meta> du tout)."""
        if self._meta_started:
            self._pending = ""
            return ""
        out = self._pending.rstrip()
        self._pending = ""
        return out

    @classmethod
    def _holdback_len(cls, text: str) -> int:
        """Longueur du suffixe à retenir : préfixe partiel de `<meta>` + whitespace avant.

        Exemples : "blabla <me" → 3 (« <me ») ; "fin.\\n\\n" → 2 (les sauts de
        ligne qui précéderaient un futur marqueur) ; "a < b" → 0.
        """
        marker = cls._MARKER
        prefix_len = 0
        for k in range(min(len(marker) - 1, len(text)), 0, -1):
            if text.endswith(marker[:k]):
                prefix_len = k
                break
        i = len(text) - prefix_len
        while i > 0 and text[i - 1] in " \t\r\n":
            i -= 1
        return len(text) - i


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

    depot_raw = data.get("depot") or {"content": None, "kind": None}
    if not isinstance(depot_raw, dict):
        raise MetaParseError("depot doit être un objet ou null")
    depot_kind = depot_raw.get("kind")
    if depot_kind is not None and depot_kind not in VALID_DEPOT_KINDS:
        raise MetaParseError(f"depot.kind invalide : {depot_kind!r}")
    depot: DepotMeta = {
        "content": _opt_str(depot_raw.get("content"), "depot.content"),
        "kind": depot_kind,
    }

    expense_raw = data.get("expense") or {
        "action": None,
        "amount": None,
        "label": None,
        "category": None,
        "recurring_key": None,
        "when": None,
        "shared": False,
        "starts_cycle": False,
    }
    if not isinstance(expense_raw, dict):
        raise MetaParseError("expense doit être un objet ou null")
    expense_action = expense_raw.get("action")
    if expense_action is not None and expense_action not in VALID_EXPENSE_ACTIONS:
        raise MetaParseError(f"expense.action invalide : {expense_action!r}")
    expense_amount = _opt_float(expense_raw.get("amount"), "expense.amount")
    if expense_amount is not None and expense_amount < 0:
        raise MetaParseError("expense.amount doit être positif")
    # `shared` est optionnel : default False si absent (rétro-compat avec
    # les LLM qui n'ont pas encore le nouveau prompt).
    raw_shared = expense_raw.get("shared", False)
    if not isinstance(raw_shared, bool):
        raise MetaParseError("expense.shared doit être un booléen")
    # `starts_cycle` est optionnel : default False. True uniquement quand le
    # LLM identifie un salaire reçu (qui réinitialise le cycle budgétaire).
    raw_starts_cycle = expense_raw.get("starts_cycle", False)
    if not isinstance(raw_starts_cycle, bool):
        raise MetaParseError("expense.starts_cycle doit être un booléen")
    expense: ExpenseMeta = {
        "action": expense_action,
        "amount": expense_amount,
        "label": _opt_str(expense_raw.get("label"), "expense.label"),
        "category": _opt_str(expense_raw.get("category"), "expense.category"),
        "recurring_key": _opt_str(expense_raw.get("recurring_key"), "expense.recurring_key"),
        "when": _opt_str(expense_raw.get("when"), "expense.when"),
        "shared": raw_shared,
        "starts_cycle": raw_starts_cycle,
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
        depot=depot,
        expense=expense,
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
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError as exc:
            raise MetaParseError(f"{field} doit être un nombre ou null") from exc
    raise MetaParseError(f"{field} doit être un nombre ou null")
