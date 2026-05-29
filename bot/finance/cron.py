"""Cron quotidien : pousse une notif iOS pour chaque récurrente non pointée.

Le job s'exécute une fois par jour à `FINANCE_REMINDER_HOUR:MINUTE` (env).
Il lit la section `finances.recurring` du YAML, et pour chaque récurrente
dont le jour prévu est atteint (ou dépassé sans pointage), il enqueue dans
`NotificationStore` une question simple :

    « Le loyer de 800€ est-il passé ? »

L'utilisateur répond ensuite via /ask ("le loyer est passé") — le pipeline
appelle `ExpenseManager.tick_recurring` et idempotence-vérifie. Si rien
n'est passé, la notif sera réenvoyée le lendemain (mais limitée à une par
mois grâce à `is_recurring_ticked_this_month` — pas de spam).

Le job est complètement tolérant aux pannes : exception → log warning,
n'interrompt pas la chaîne des notifs suivantes.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from bot.finance.budget import next_recurring_occurrence
from bot.finance.config import RecurringItem, extract_finance_config
from bot.logging_conf import get_logger

if TYPE_CHECKING:
    from bot.finance.manager import ExpenseManager
    from bot.notifications.store import NotificationStore
    from bot.profile import UserProfile

log = get_logger(__name__)


class FinanceReminderJob:
    """Cron quotidien : notifie pour chaque récurrente non-pointée du jour."""

    def __init__(
        self,
        *,
        profile: UserProfile,
        expenses: ExpenseManager,
        notifications: NotificationStore,
        timezone: str,
    ) -> None:
        self._profile = profile
        self._expenses = expenses
        self._notifications = notifications
        self._tz = timezone

    async def run(self) -> None:
        """Point d'entrée du cron, appelé par APScheduler."""
        try:
            cfg = extract_finance_config(self._profile.data)
        except Exception as exc:
            log.warning("finance_cron_config_failed", error=str(exc))
            return
        if not cfg.is_configured:
            return

        today = datetime.now(ZoneInfo(self._tz)).date()
        cycle_start, _ = await self._expenses.current_cycle_bounds(today)
        pushed = 0
        for item in cfg.recurring:
            try:
                if await self._maybe_push(item, today, cycle_start):
                    pushed += 1
            except Exception as exc:
                # Une notif ratée ne doit pas bloquer les suivantes.
                log.warning(
                    "finance_cron_item_failed",
                    key=item.key,
                    error=str(exc),
                )
        log.info("finance_cron_run", pushed=pushed, today=today.isoformat())

    async def _maybe_push(self, item: RecurringItem, today: date, cycle_start: date) -> bool:
        """Pousse la notif si la récurrente est due dans le cycle (ou en retard).

        L'échéance est projetée dans le cycle courant (`next_recurring_occurrence`) :
        une récurrente « le 5 » sur un cycle démarré le 28/04 est due le
        05/05. Le rappel part à partir de l'échéance et tous les jours
        suivants jusqu'au pointage ; `is_recurring_ticked_in_cycle` garantit
        qu'on n'écrit pas plusieurs lignes.
        """
        due = next_recurring_occurrence(item.day, cycle_start)
        if today < due:
            return False
        if await self._expenses.is_recurring_ticked_in_cycle(item.key, today):
            return False

        text = _format_question(item, due, today)
        await self._notifications.add(
            text=text,
            title="💸 Récurrente",
            priority=0,
            sound="pushover",
        )
        log.info("finance_reminder_pushed", key=item.key, due=due.isoformat())
        return True


def _format_question(item: RecurringItem, due: date, today: date) -> str:
    overdue = " (en retard)" if today > due else ""
    amount = _format_amount_eur(item.amount_cents)
    verb = "versé" if item.kind == "saving" else "passé"
    return (
        f"{item.label} ({amount}, prévu le {due.day}){overdue} — "
        f"déjà {verb} ce cycle ? Réponds « {item.label.lower()} est {verb} » pour confirmer."
    )


def _format_amount_eur(cents: int) -> str:
    if cents % 100 == 0:
        return f"{cents // 100}€"
    euros = cents / 100
    return f"{euros:.2f}".replace(".", ",") + "€"
