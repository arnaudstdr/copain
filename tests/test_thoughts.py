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


# --- close() ----------------------------------------------------------------


async def test_close_sets_processed_at(manager: ThoughtManager) -> None:
    thought = await manager.create("j'ai peur pour le contrôle technique", kind="worry")
    assert thought.processed_at is None

    ok = await manager.close(thought.id)
    assert ok is True

    recent = await manager.list_recent()
    assert recent[0].id == thought.id
    assert recent[0].processed_at is not None


async def test_close_is_idempotent(manager: ThoughtManager) -> None:
    thought = await manager.create("souci déjà réglé", kind="worry")
    assert await manager.close(thought.id) is True

    first_processed_at = (await manager.list_recent())[0].processed_at
    assert first_processed_at is not None

    # Re-clôture : True, sans toucher le processed_at initial.
    assert await manager.close(thought.id) is True
    assert (await manager.list_recent())[0].processed_at == first_processed_at


async def test_close_unknown_id_returns_false(manager: ThoughtManager) -> None:
    ok = await manager.close(9999)
    assert ok is False


# --- list_open() ------------------------------------------------------------


async def test_list_open_excludes_closed(manager: ThoughtManager) -> None:
    open_t = await manager.create("souci ouvert", kind="worry")
    closed_t = await manager.create("souci clos", kind="worry")
    await manager.close(closed_t.id)

    open_thoughts = await manager.list_open()
    assert [t.id for t in open_thoughts] == [open_t.id]


async def test_list_open_filters_by_kinds(manager: ThoughtManager) -> None:
    worry = await manager.create("un souci", kind="worry")
    await manager.create("une idée", kind="idea")
    await manager.create("dépôt sans kind")

    worries = await manager.list_open(kinds=["worry"])
    assert [t.id for t in worries] == [worry.id]


async def test_list_open_chronological_inverse_and_limit(manager: ThoughtManager) -> None:
    await manager.create("premier", kind="worry")
    await asyncio.sleep(0.01)
    await manager.create("deuxième", kind="worry")
    await asyncio.sleep(0.01)
    await manager.create("troisième", kind="worry")

    open_thoughts = await manager.list_open(limit=2)
    assert len(open_thoughts) == 2
    assert open_thoughts[0].content == "troisième"
    assert open_thoughts[1].content == "deuxième"


async def test_list_open_empty_when_all_closed(manager: ThoughtManager) -> None:
    t = await manager.create("seul dépôt", kind="idea")
    await manager.close(t.id)
    assert await manager.list_open() == []


# --- mark_surfaced() --------------------------------------------------------


async def test_mark_surfaced_targets_only_given_ids(manager: ThoughtManager) -> None:
    t1 = await manager.create("restitué", kind="idea")
    t2 = await manager.create("pas restitué", kind="idea")

    await manager.mark_surfaced([t1.id])

    by_id = {t.id: t for t in await manager.list_recent()}
    assert by_id[t1.id].surfaced_at is not None
    assert by_id[t2.id].surfaced_at is None


async def test_mark_surfaced_empty_list_is_noop(manager: ThoughtManager) -> None:
    t = await manager.create("dépôt", kind="note")
    await manager.mark_surfaced([])
    assert (await manager.list_recent())[0].surfaced_at is None
    assert t.surfaced_at is None


# --- Migration surfaced_at (D5) ----------------------------------------------


async def test_init_schema_migrates_legacy_table(tmp_data_dir: Path) -> None:
    """Une table `thoughts` pré-existante sans `surfaced_at` est migrée.

    Reproduit une base créée avant l'ajout de la colonne : `create_all`
    n'altère pas une table existante, c'est l'ALTER TABLE idempotent de
    `init_schema` qui doit ajouter la colonne.
    """
    from sqlalchemy import text

    engine = create_shared_engine(tmp_data_dir / "tasks.db")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE thoughts ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "content VARCHAR NOT NULL, "
                "kind VARCHAR, "
                "created_at DATETIME NOT NULL, "
                "processed_at DATETIME)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO thoughts (content, kind, created_at) "
                "VALUES ('dépôt legacy', 'worry', '2026-05-01 10:00:00')"
            )
        )

    mgr = ThoughtManager(engine)
    # Idempotence : deux appels successifs ne lèvent pas.
    await mgr.init_schema()
    await mgr.init_schema()

    # La colonne existe : la ligne legacy est lisible, surfaced_at NULL.
    recent = await mgr.list_recent()
    assert len(recent) == 1
    assert recent[0].surfaced_at is None

    # Et les nouvelles méthodes fonctionnent sur la base migrée.
    await mgr.mark_surfaced([recent[0].id])
    assert (await mgr.list_recent())[0].surfaced_at is not None

    await engine.dispose()
