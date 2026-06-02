"""Tests de `ReminderScheduler.add_interval_job` (jobs toutes les N min)."""

from __future__ import annotations

from pathlib import Path

from apscheduler.triggers.interval import IntervalTrigger

from bot.tasks.scheduler import ReminderScheduler


async def _noop() -> None:
    return None


def test_add_interval_job_registers_in_memory_store(tmp_path: Path) -> None:
    scheduler = ReminderScheduler(
        tmp_path / "scheduler.db",
        notifications_db_path=tmp_path / "tasks.db",
        timezone="Europe/Paris",
    )
    scheduler.add_interval_job(job_id="tick", func=_noop, minutes=30)

    job = scheduler._scheduler.get_job("tick")
    assert job is not None
    assert isinstance(job.trigger, IntervalTrigger)
    assert job.trigger.interval.total_seconds() == 30 * 60


def test_add_interval_job_uses_memory_jobstore(tmp_path: Path) -> None:
    """Vérifie que le job ne finit pas dans le SQLAlchemyJobStore (non-sérialisable)."""
    scheduler = ReminderScheduler(
        tmp_path / "scheduler.db",
        notifications_db_path=tmp_path / "tasks.db",
        timezone="Europe/Paris",
    )
    scheduler.add_interval_job(job_id="tick", func=_noop, minutes=30)

    memory_jobs = scheduler._scheduler.get_jobs(jobstore="memory")
    assert any(j.id == "tick" for j in memory_jobs)


def test_scheduler_db_uses_wal_mode(tmp_path: Path) -> None:
    """La base scheduler.db doit être en WAL (mode persistant du fichier)."""
    import sqlite3

    db_path = tmp_path / "scheduler.db"
    ReminderScheduler(
        db_path,
        notifications_db_path=tmp_path / "tasks.db",
        timezone="Europe/Paris",
    )
    with sqlite3.connect(db_path) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
