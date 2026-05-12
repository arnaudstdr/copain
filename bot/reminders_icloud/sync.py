"""Job de sync périodique : tâches cochées côté iPhone → DB locale."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bot.logging_conf import get_logger

if TYPE_CHECKING:
    from bot.reminders_icloud.client import ICloudRemindersClient
    from bot.tasks.manager import TaskManager

log = get_logger(__name__)


async def sync_completed_tasks(
    tasks: TaskManager,
    reminders: ICloudRemindersClient,
) -> int:
    """Marque comme completed côté DB les tasks cochées dans Apple Rappels.

    Le job tourne en cron (intervalle configurable, défaut 5 min).
    `tasks.complete` est idempotent : si la task est déjà completed côté
    DB, le call est un no-op et compte pour 0 dans le return. C'est utile
    pour les tasks complétées via le pipeline classique (qui les coche
    déjà en DB et propage vers iCloud — au prochain sync on les revoit
    "completed côté iCloud" sans rien refaire).
    """
    if not reminders.is_connected:
        log.warning("reminders_sync_skipped", reason="not_connected")
        return 0

    completed_uids = await reminders.list_completed_uids()
    count = 0
    for task_id in completed_uids:
        completed = await tasks.complete(task_id)
        if completed:
            count += 1
    log.info("reminders_sync_done", completed=count, candidates=len(completed_uids))
    return count
