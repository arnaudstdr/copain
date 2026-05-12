"""Modèle métier pour une tâche miroitée dans Apple Rappels (VTODO)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ICloudReminder:
    """Représentation d'un VTODO côté iCloud, lu via CalDAV.

    On ne stocke pas tout le contenu iCal (description, location, etc.) :
    seuls les champs utiles pour la sync avec la DB locale sont exposés.
    """

    uid: str
    title: str
    due_at: datetime | None  # timezone-aware, None si pas de date
    completed: bool
