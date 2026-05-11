"""Planificateur de jobs : rappels (persistés) et cron (mémoire).

Les rappels écrivent dans la file `pending_notifications` (SQLite),
consommée par `GET /notifications` côté client iOS. Le chemin de la base
est sérialisé dans les args du job (au même titre que le contenu) afin de
pouvoir reconstruire un `NotificationStore` lors d'une exécution post-
redémarrage : aucune référence à un objet vivant n'est picklée, seulement
des primitifs (str, int).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_ERROR, JobExecutionEvent
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.db import create_shared_engine
from bot.logging_conf import get_logger
from bot.notifications.pushover import PushoverClient
from bot.notifications.store import NotificationStore
from bot.sentry_setup import capture_exception

log = get_logger(__name__)

REMINDER_PREFIX = "⏰ Rappel : "


async def _send_reminder(
    db_path: str,
    content: str,
    pushover_token: str = "",
    pushover_user: str = "",
) -> None:
    """Empile un rappel dans `pending_notifications` et le pousse via Pushover.

    Sérialisable (jobstore SQLAlchemy) : seuls des primitifs sont passés en args.
    Les jobs persistés avant l'ajout de Pushover utilisent les valeurs par défaut
    (token/user vides = pas de push, comportement identique à l'ancienne version).
    """
    engine = create_shared_engine(Path(db_path))
    pushover = PushoverClient(token=pushover_token, user=pushover_user)
    store = NotificationStore(engine, pushover=pushover)
    try:
        await store.add(
            f"{REMINDER_PREFIX}{content}",
            title="Rappel",
            priority=1,
            sound="pushover",
        )
    finally:
        await engine.dispose()


class ReminderScheduler:
    """Ajoute/supprime des jobs de rappel persistés entre redémarrages."""

    def __init__(
        self,
        db_path: Path,
        notifications_db_path: Path,
        timezone: str = "Europe/Paris",
        pushover_token: str = "",
        pushover_user: str = "",
    ) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._timezone = timezone
        self._notifications_db_path = notifications_db_path
        self._pushover_token = pushover_token
        self._pushover_user = pushover_user
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
        self._scheduler.add_listener(self._on_job_error, EVENT_JOB_ERROR)

    @staticmethod
    def _on_job_error(event: JobExecutionEvent) -> None:
        """Log + remonte à Sentry les exceptions levées par un job APScheduler."""
        exc = event.exception
        log.error("job_error", job_id=event.job_id, error=str(exc))
        if exc is not None:
            capture_exception(exc, source="apscheduler", job_id=event.job_id)

    def start(self) -> None:
        self._scheduler.start()
        log.info("scheduler_started")

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    def add_reminder(
        self,
        task_id: int,
        due_at: datetime,
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
            args=[str(self._notifications_db_path), content, self._pushover_token, self._pushover_user],
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
