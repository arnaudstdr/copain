"""Tests du `NewsDigestStore` (persistance SQLite du digest actu du jour)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from bot.db import create_shared_engine
from bot.news.store import NewsDigestStore

TODAY = date(2026, 7, 21)
YESTERDAY = date(2026, 7, 20)


@pytest.fixture
async def engine(tmp_data_dir: Path) -> AsyncIterator[AsyncEngine]:
    eng = create_shared_engine(tmp_data_dir / "tasks.db")
    yield eng
    await eng.dispose()


@pytest.fixture
async def store(engine: AsyncEngine) -> NewsDigestStore:
    s = NewsDigestStore(engine)
    await s.init_schema()
    return s


async def test_get_absent_returns_none(store: NewsDigestStore) -> None:
    assert await store.get(TODAY) is None


async def test_save_then_get(store: NewsDigestStore) -> None:
    await store.save(TODAY, "## Actu\n- un article")

    digest = await store.get(TODAY)
    assert digest is not None
    assert digest.markdown == "## Actu\n- un article"
    assert digest.digest_date == TODAY.isoformat()


async def test_get_returns_aware_utc(store: NewsDigestStore) -> None:
    await store.save(TODAY, "## Actu")

    digest = await store.get(TODAY)
    assert digest is not None
    assert digest.fetched_at.tzinfo is not None
    assert digest.fetched_at.utcoffset() == UTC.utcoffset(None)


async def test_save_replaces_same_day(store: NewsDigestStore) -> None:
    await store.save(TODAY, "ancien")
    await store.save(TODAY, "nouveau")

    digest = await store.get(TODAY)
    assert digest is not None
    assert digest.markdown == "nouveau"


async def test_save_replaces_other_day(store: NewsDigestStore) -> None:
    """La table ne conserve jamais plus d'un digest : un save d'un autre jour
    efface le précédent (cf. SPEC décision 2)."""
    await store.save(YESTERDAY, "hier")
    await store.save(TODAY, "aujourd'hui")

    assert await store.get(YESTERDAY) is None
    today = await store.get(TODAY)
    assert today is not None
    assert today.markdown == "aujourd'hui"


async def test_purge_except_removes_other_days(store: NewsDigestStore) -> None:
    await store.save(YESTERDAY, "hier")

    deleted = await store.purge_except(TODAY)

    assert deleted == 1
    assert await store.get(YESTERDAY) is None


async def test_purge_except_keeps_current_day(store: NewsDigestStore) -> None:
    await store.save(TODAY, "aujourd'hui")

    deleted = await store.purge_except(TODAY)

    assert deleted == 0
    today = await store.get(TODAY)
    assert today is not None
    assert today.markdown == "aujourd'hui"


async def test_purge_except_empty_table(store: NewsDigestStore) -> None:
    assert await store.purge_except(TODAY) == 0
