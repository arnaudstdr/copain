"""Règles métier de la proactivité, purement fonctionnelles (sans I/O).

Chaque fonction prend des données déjà collectées (forecast horaire, events
calendrier) et retourne une `Notification` à envoyer, ou `None` si aucun
déclencheur n'est atteint. Le `ProactivityService` orchestre l'appel et
applique les garde-fous (fenêtre horaire, cooldown, budget).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from bot.briefing.weather import HourlyPrecipitation
from bot.calendar.models import CalendarEvent

NotificationKind = Literal["rain", "event"]


@dataclass(frozen=True, slots=True)
class Notification:
    kind: NotificationKind
    text: str
    event_uid: str | None = None


def evaluate_rain(
    hourly: list[HourlyPrecipitation],
    *,
    mm_threshold: float = 0.3,
    proba_threshold: int = 60,
) -> Notification | None:
    """Retourne une notif si la prochaine heure passe l'un des seuils.

    On regarde la **première** heure disponible (supposée être l'heure courante
    ou la suivante) : c'est le déclencheur le plus pertinent pour prévenir
    juste avant une averse. Les heures suivantes dans la liste sont ignorées.
    """
    if not hourly:
        return None
    first = hourly[0]
    if first.mm < mm_threshold and first.probability_pct < proba_threshold:
        return None
    hhmm = first.time.strftime("%H:%M")
    text = (
        f"☔ Parapluie — pluie probable vers {hhmm} "
        f"(≈{first.mm:.1f} mm, {first.probability_pct} %)"
    )
    return Notification(kind="rain", text=text)


def evaluate_upcoming_event(
    events: list[CalendarEvent],
    now: datetime,
    *,
    min_minutes: int = 45,
    max_minutes: int = 75,
) -> Notification | None:
    """Retourne une notif pour le premier event démarrant dans la fenêtre.

    Les events hors fenêtre (trop tôt, trop tard) sont ignorés. L'`uid` est
    remonté dans `Notification.event_uid` pour permettre la dédup par event
    dans le service (on ne pré-notifie chaque event qu'une seule fois).
    """
    for event in events:
        delta_min = (event.start - now).total_seconds() / 60.0
        if delta_min < min_minutes or delta_min > max_minutes:
            continue
        hhmm = event.start.strftime("%H:%M")
        minutes = round(delta_min)
        text = f'📅 RDV "{event.title}" à {hhmm} (dans {minutes} min)'
        return Notification(kind="event", text=text, event_uid=event.uid)
    return None
