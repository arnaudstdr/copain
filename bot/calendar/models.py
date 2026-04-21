"""Modèle métier pour un événement calendrier (issu de CalDAV/VEVENT)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    uid: str
    title: str
    start: datetime  # aware
    end: datetime  # aware
    location: str | None
    description: str | None
    calendar_name: str

    def __repr__(self) -> str:
        return (
            f"CalendarEvent({self.title!r}, "
            f"{self.start.isoformat()} → {self.end.isoformat()}, "
            f"cal={self.calendar_name!r})"
        )
