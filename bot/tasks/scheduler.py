"""Planificateur de jobs : rappels (persistés) et cron (mémoire)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.logging_conf import get_logger
from bot.telegram_sender import send_message

if TYPE_CHECKING:
    from telegram.ext import Application

log = get_logger(__name__)

REMINDER_PREFIX = "⏰ Rappel : "


async def _send_reminder(chat_id: int, content: str) -> None:
    """Envoie le message de rappel via l'API Telegram.

    Cette fonction est rappelée par APScheduler à l'échéance. Le token n'est
    PAS passé en argument (il serait picklé dans `scheduler.db`) ; il est lu
    via `os.environ` dans `bot.telegram_sender.send_message`.
    """
    await send_message(chat_id=chat_id, text=f"{REMINDER_PREFIX}{content}")


class ReminderScheduler:
    """Ajoute/supprime des jobs de rappel persistés entre redémarrages."""

    def __init__(self, db_path: Path, timezone: str = "Europe/Paris") -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._timezone = timezone
        # default = rappels one-shot persistés (SQLAlchemy)
        # memory = cron/recurrent (closures, non-sérialisables, re-planifiés au startup)
        self._scheduler = AsyncIOScheduler(
            jobstores={
                "default": SQLAlchemyJobStore(url=f"sqlite:///{db_path}"),
                "memory": MemoryJobStore(),
            },
            timezone=ZoneInfo(timezone),
            job_defaults={
                "misfire_grace_time": 3600,  # 1 h de tolérance : rappels persistés envoyés même après un redémarrage tardif
            },
        )

    def start(self) -> None:
        self._scheduler.start()
        log.info("scheduler_started")

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    def add_reminder(
        self,
        task_id: int,
        due_at: datetime,
        chat_id: int,
        content: str,
    ) -> None:
        now = datetime.now(ZoneInfo(self._timezone))
        if due_at <= now:
            log.warning(
                "reminder_due_in_past",
                task_id=task_id,
                due_at=due_at.isoformat(),
                now=now.isoformat(),
            )
        self._scheduler.add_job(
            _send_reminder,
            trigger="date",
            run_date=due_at,
            args=[chat_id, content],
            id=f"task-{task_id}",
            replace_existing=True,
        )
        log.info("reminder_scheduled", task_id=task_id, due_at=due_at.isoformat())

    def cancel_reminder(self, task_id: int) -> None:
        job_id = f"task-{task_id}"
        if self._scheduler.get_job(job_id) is not None:
            self._scheduler.remove_job(job_id)
            log.info("reminder_cancelled", task_id=task_id)

    def add_cron_job(
        self,
        job_id: str,
        func: Callable[..., Awaitable[None]],
        hour: int,
        minute: int,
    ) -> None:
        """Ajoute un job cron en mémoire (re-planifié au startup).

        Utilisé pour les tâches récurrentes non-sérialisables (closures qui capturent
        des services). Le SQLAlchemyJobStore exige la sérialisation ; on le court-circuite
        en utilisant un MemoryJobStore dédié.
        """
        self._scheduler.add_job(
            func,
            trigger="cron",
            hour=hour,
            minute=minute,
            id=job_id,
            replace_existing=True,
            jobstore="memory",
        )
        log.info("cron_job_scheduled", job_id=job_id, hour=hour, minute=minute)

    def add_interval_job(
        self,
        job_id: str,
        func: Callable[..., Awaitable[None]],
        minutes: int,
    ) -> None:
        """Ajoute un job récurrent "toutes les N minutes" dans le MemoryJobStore.

        Même logique que `add_cron_job` (closures non-sérialisables, re-planifié
        au startup). Utilisé par le service de proactivité qui tick à intervalle
        régulier pour évaluer ses règles.
        """
        self._scheduler.add_job(
            func,
            trigger="interval",
            minutes=minutes,
            id=job_id,
            replace_existing=True,
            jobstore="memory",
        )
        log.info("interval_job_scheduled", job_id=job_id, minutes=minutes)

    def attach_application(self, _app: Application[Any, Any, Any, Any, Any, Any]) -> None:
        """Hook réservé pour de futurs jobs qui auraient besoin de l'Application."""
        # Pas utilisé pour l'instant : le job reconstruit un Bot à partir du token.
