"""Tests du modèle NotificationLog (création, lecture, dédup par event_uid)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.db import create_shared_engine
from bot.proactivity.models import NotificationLog
from bot.tasks.manager import TaskManager


async def test_notification_log_roundtrip(tmp_data_dir: Path) -> None:
    engine = create_shared_engine(tmp_data_dir / "tasks.db")
    # `init_schema` crée toutes les tables de `Base.metadata` — y compris NotificationLog
    # (importé par le module `bot.proactivity`).
    await TaskManager(engine).init_schema()
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    now = datetime.now(UTC)
    async with sessionmaker() as session:
        session.add(NotificationLog(kind="rain", event_uid=None, sent_at=now))
        session.add(NotificationLog(kind="event", event_uid="abc-123", sent_at=now))
        await session.commit()

    async with sessionmaker() as session:
        result = await session.execute(select(NotificationLog).order_by(NotificationLog.id))
        rows = list(result.scalars().all())

    assert len(rows) == 2
    assert rows[0].kind == "rain"
    assert rows[0].event_uid is None
    assert rows[1].kind == "event"
    assert rows[1].event_uid == "abc-123"

    await engine.dispose()


async def test_notification_log_event_uid_index_allows_filter(tmp_data_dir: Path) -> None:
    engine = create_shared_engine(tmp_data_dir / "tasks.db")
    await TaskManager(engine).init_schema()
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    now = datetime.now(UTC)
    async with sessionmaker() as session:
        session.add(NotificationLog(kind="event", event_uid="uid-1", sent_at=now))
        session.add(NotificationLog(kind="event", event_uid="uid-2", sent_at=now))
        await session.commit()

    async with sessionmaker() as session:
        result = await session.execute(
            select(NotificationLog).where(
                NotificationLog.kind == "event", NotificationLog.event_uid == "uid-1"
            )
        )
        rows = list(result.scalars().all())

    assert len(rows) == 1
    assert rows[0].event_uid == "uid-1"

    await engine.dispose()
