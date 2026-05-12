"""CRUD async sur la table `location_events`.

Le store expose deux usages :
- `record_event` : appelée par l'endpoint HTTP quand iOS envoie une
  transition (arrived/left).
- `get_current_location` : dérive l'état courant pour injection dans
  le system prompt (logique "dernier event gagne", tolérante à la
  perte ponctuelle d'une notification iOS).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from bot.locations.models import LocationEvent
from bot.locations.presence import LocationPresence
from bot.logging_conf import get_logger
from bot.tasks.models import Base

log = get_logger(__name__)


class LocationEventStore:
    """File async des events arrived/left venant des automations iOS."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)

    async def init_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def record_event(
        self,
        event_type: Literal["arrived", "left"],
        place: str,
        lat: float | None = None,
        lon: float | None = None,
        occurred_at: datetime | None = None,
    ) -> LocationEvent:
        """Persiste une transition de localisation envoyée par iOS."""
        ts = occurred_at if occurred_at is not None else datetime.now(UTC)
        event = LocationEvent(
            event_type=event_type,
            place=place,
            lat=lat,
            lon=lon,
            occurred_at=ts,
        )
        async with self._sessionmaker() as session:
            session.add(event)
            await session.commit()
            await session.refresh(event)
        log.info(
            "location_event_recorded",
            event_type=event_type,
            place=place,
            occurred_at=ts.isoformat(),
        )
        return event

    async def get_current_location(self) -> LocationPresence | None:
        """Retourne la localisation courante dérivée du dernier event.

        Logique simple "dernier event gagne" :
        - Si la dernière transition est un `arrived` → on est à ce lieu.
        - Si la dernière transition est un `left` → on est entre deux lieux
          (None).
        - Si aucune transition n'a été enregistrée → None.

        Cette logique tolère la perte d'un `left` (si iOS rate la notif
        de départ avant un `arrived` ailleurs, on bascule quand même au
        nouveau lieu) au prix de quelques faux positifs "tu es à X" après
        un `left` perdu sans nouveau `arrived`.
        """
        async with self._sessionmaker() as session:
            stmt = select(LocationEvent).order_by(LocationEvent.occurred_at.desc()).limit(1)
            result = await session.execute(stmt)
            last = result.scalar_one_or_none()
        if last is None or last.event_type != "arrived":
            return None
        return LocationPresence(
            place=last.place,
            arrived_at=last.occurred_at,
            lat=last.lat,
            lon=last.lon,
        )

    async def list_recent(self, limit: int = 20) -> Sequence[LocationEvent]:
        """Retourne les `limit` derniers events, plus récents en tête."""
        async with self._sessionmaker() as session:
            stmt = select(LocationEvent).order_by(LocationEvent.occurred_at.desc()).limit(limit)
            result = await session.execute(stmt)
            return result.scalars().all()  # type: ignore[no-any-return, unused-ignore]
