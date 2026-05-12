"""Tests du `LocationEventStore` (file SQLite des events de localisation iOS)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from bot.db import create_shared_engine
from bot.locations.store import LocationEventStore


@pytest.fixture
async def engine(tmp_data_dir: Path) -> AsyncIterator[AsyncEngine]:
    eng = create_shared_engine(tmp_data_dir / "tasks.db")
    yield eng
    await eng.dispose()


@pytest.fixture
async def store(engine: AsyncEngine) -> LocationEventStore:
    s = LocationEventStore(engine)
    await s.init_schema()
    return s


async def test_record_event_then_current_location(store: LocationEventStore) -> None:
    await store.record_event("arrived", "home")
    presence = await store.get_current_location()
    assert presence is not None
    assert presence.place == "home"
    assert presence.lat is None and presence.lon is None


async def test_left_event_clears_current_location(store: LocationEventStore) -> None:
    """Après un `left`, l'utilisateur est considéré entre deux lieux (None)."""
    await store.record_event("arrived", "home")
    await store.record_event("left", "home")
    assert await store.get_current_location() is None


async def test_arrived_overrides_previous_arrived(store: LocationEventStore) -> None:
    """Si une transition `left` est perdue côté iOS, le prochain `arrived` gagne.

    Ce comportement est volontaire pour rester tolérant aux drops réseau ;
    l'alternative serait de bloquer sur un `left` manquant, plus rigide.
    """
    await store.record_event("arrived", "home")
    await store.record_event("arrived", "work")
    presence = await store.get_current_location()
    assert presence is not None
    assert presence.place == "work"


async def test_get_current_location_uses_latest_by_occurred_at(
    store: LocationEventStore,
) -> None:
    """L'ordre dans `occurred_at` est respecté, pas l'ordre d'insertion."""
    now = datetime.now(UTC)
    # On insère dans le désordre, mais on indique des occurred_at explicites.
    await store.record_event("arrived", "work", occurred_at=now - timedelta(hours=1))
    await store.record_event("arrived", "home", occurred_at=now - timedelta(hours=2))
    presence = await store.get_current_location()
    assert presence is not None
    assert presence.place == "work"


async def test_empty_store_returns_none(store: LocationEventStore) -> None:
    assert await store.get_current_location() is None


async def test_list_recent_orders_desc(store: LocationEventStore) -> None:
    await store.record_event("arrived", "home")
    await store.record_event("left", "home")
    await store.record_event("arrived", "work")

    recent = await store.list_recent(limit=10)
    places = [(e.event_type, e.place) for e in recent]
    assert places == [("arrived", "work"), ("left", "home"), ("arrived", "home")]


async def test_record_event_with_coords(store: LocationEventStore) -> None:
    await store.record_event("arrived", "home", lat=48.26, lon=7.45)
    presence = await store.get_current_location()
    assert presence is not None
    assert presence.lat == pytest.approx(48.26)
    assert presence.lon == pytest.approx(7.45)
