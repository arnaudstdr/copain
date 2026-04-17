"""Planificateur de rappels : APScheduler persisté en SQLAlchemy."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.logging_conf import get_logger

if TYPE_CHECKING:
    from telegram.ext import Application

log = get_logger(__name__)


async def _send_reminder(bot_token: str, chat_id: int, content: str) -> None:
    """Envoie le message de rappel via l'API Telegram.

    Cette fonction est rappelée par APScheduler à l'échéance. Elle ne peut pas
    capturer directement `Application` (non sérialisable dans le JobStore), d'où
    la construction d'un `Bot` éphémère à partir du token.
    """
    from telegram import Bot

    bot = Bot(token=bot_token)
    async with bot:
        await bot.send_message(chat_id=chat_id, text=f"⏰ Rappel : {content}")


class ReminderScheduler:
    """Ajoute/supprime des jobs de rappel persistés entre redémarrages."""

    def __init__(self, db_path: Path, bot_token: str, timezone: str = "Europe/Paris") -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        jobstore = SQLAlchemyJobStore(url=f"sqlite:///{db_path}")
        self._scheduler = AsyncIOScheduler(
            jobstores={"default": jobstore},
            timezone=ZoneInfo(timezone),
        )
        self._bot_token = bot_token

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
        self._scheduler.add_job(
            _send_reminder,
            trigger="date",
            run_date=due_at,
            args=[self._bot_token, chat_id, content],
            id=f"task-{task_id}",
            replace_existing=True,
        )
        log.info("reminder_scheduled", task_id=task_id, due_at=due_at.isoformat())

    def cancel_reminder(self, task_id: int) -> None:
        job_id = f"task-{task_id}"
        if self._scheduler.get_job(job_id) is not None:
            self._scheduler.remove_job(job_id)

    def attach_application(self, _app: Application) -> None:
        """Hook réservé pour de futurs jobs qui auraient besoin de l'Application."""
        # Pas utilisé pour l'instant : le job reconstruit un Bot à partir du token.
