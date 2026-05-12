"""Dataclass représentant la localisation courante de l'utilisateur."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class LocationPresence:
    """État dérivé "où est l'utilisateur en ce moment ?".

    Renvoyé par `LocationEventStore.get_current_location()` et injecté
    dans le system prompt à chaque appel LLM. Différent de l'event brut
    `LocationEvent` qui décrit une transition (arrivée/départ ponctuelle).
    """

    place: str
    arrived_at: datetime  # timezone-aware
    lat: float | None
    lon: float | None
