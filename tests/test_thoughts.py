"""Tests du ThoughtManager sur une base SQLite temporaire."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bot.db import create_shared_engine
from bot.thoughts.manager import ThoughtManager


@pytest.fixture
async def manager(tmp_data_dir: Path) -> ThoughtManager:
    engine = create_shared_engine(tmp_data_dir / "tasks.db")
    mgr = ThoughtManager(engine)
    await mgr.init_schema()
    yield mgr
    await engine.dispose()


async def test_create_thought_without_kind(manager: ThoughtManager) -> None:
    thought = await manager.create("je dois pas oublier l'assurance auto")
    assert thought.id is not None
    assert thought.content == "je dois pas oublier l'assurance auto"
    assert thought.kind is None
    assert thought.processed_at is None
    assert thought.created_at is not None


async def test_create_thought_with_kind(manager: ThoughtManager) -> None:
    thought = await manager.create("j'ai peur pour les finances de mon fils", kind="worry")
    assert thought.kind == "worry"


async def test_create_rejects_invalid_kind(manager: ThoughtManager) -> None:
    with pytest.raises(ValueError, match="kind invalide"):
        await manager.create("test", kind="banana")


async def test_list_recent_returns_chronological_inverse(manager: ThoughtManager) -> None:
    await manager.create("première pensée", kind="note")
    # Petit délai pour garantir un created_at strictement différent (SQLite
    # peut stocker la même seconde sur deux inserts consécutifs très rapprochés).
    await asyncio.sleep(0.01)
    await manager.create("deuxième pensée", kind="idea")
    await asyncio.sleep(0.01)
    await manager.create("troisième pensée", kind="worry")

    recent = await manager.list_recent(limit=10)
    assert len(recent) == 3
    assert recent[0].content == "troisième pensée"
    assert recent[2].content == "première pensée"


async def test_list_recent_respects_limit(manager: ThoughtManager) -> None:
    for i in range(5):
        await manager.create(f"pensée {i}")
    recent = await manager.list_recent(limit=2)
    assert len(recent) == 2


async def test_list_since_filters_by_date(manager: ThoughtManager) -> None:
    old_cutoff = datetime.now(UTC) - timedelta(minutes=1)
    await manager.create("ancien dépôt")
    await asyncio.sleep(0.01)
    cutoff = datetime.now(UTC)
    await asyncio.sleep(0.01)
    await manager.create("nouveau dépôt 1")
    await manager.create("nouveau dépôt 2")

    since_old = await manager.list_since(old_cutoff)
    assert len(since_old) == 3

    since_cutoff = await manager.list_since(cutoff)
    assert len(since_cutoff) == 2
    assert all("nouveau" in t.content for t in since_cutoff)


async def test_list_recent_empty_when_no_thoughts(manager: ThoughtManager) -> None:
    recent = await manager.list_recent()
    assert recent == []
