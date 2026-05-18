"""Tests du `NotificationStore` (file SQLite des notifications poussées)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from bot.db import create_shared_engine
from bot.notifications.store import NotificationStore


@pytest.fixture
async def engine(tmp_data_dir: Path) -> AsyncIterator[AsyncEngine]:
    eng = create_shared_engine(tmp_data_dir / "tasks.db")
    yield eng
    await eng.dispose()


@pytest.fixture
async def store(engine: AsyncEngine) -> NotificationStore:
    s = NotificationStore(engine)
    await s.init_schema()
    return s


async def test_add_then_get_unread(store: NotificationStore) -> None:
    await store.add("Premier rappel")
    await store.add("Deuxième rappel")

    unread = await store.get_unread()
    assert [n.text for n in unread] == ["Premier rappel", "Deuxième rappel"]
    assert all(n.read_at is None for n in unread)


async def test_mark_read_removes_from_unread(store: NotificationStore) -> None:
    await store.add("Un")
    await store.add("Deux")
    unread = await store.get_unread()
    assert len(unread) == 2

    await store.mark_read([unread[0].id])

    remaining = await store.get_unread()
    assert [n.text for n in remaining] == ["Deux"]


async def test_mark_read_with_empty_list_is_noop(store: NotificationStore) -> None:
    await store.add("X")
    await store.mark_read([])
    assert len(await store.get_unread()) == 1


async def test_get_unread_orders_by_created_at(store: NotificationStore) -> None:
    """Les plus anciennes notifications sortent en tête (FIFO)."""
    await store.add("Première")
    await store.add("Suivante")
    await store.add("Dernière")

    unread = await store.get_unread()
    assert [n.text for n in unread] == ["Première", "Suivante", "Dernière"]


async def test_unread_excludes_already_read(store: NotificationStore) -> None:
    """get_unread() ne renvoie pas les notifs marquées comme lues."""
    await store.add("A")
    await store.add("B")
    ids = [n.id for n in await store.get_unread()]
    await store.mark_read(ids)
    assert await store.get_unread() == []


async def test_count_unread_does_not_mutate(store: NotificationStore) -> None:
    """count_unread() ne marque pas comme lu (utilisé par le dashboard)."""
    await store.add("X")
    await store.add("Y")
    assert await store.count_unread() == 2
    assert await store.count_unread() == 2  # idempotent
    assert len(await store.get_unread()) == 2  # toujours non lues


async def test_count_unread_excludes_read(store: NotificationStore) -> None:
    await store.add("A")
    await store.add("B")
    unread = await store.get_unread()
    await store.mark_read([unread[0].id])
    assert await store.count_unread() == 1
