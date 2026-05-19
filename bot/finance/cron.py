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

import calendar as _calendar
from datetime import date, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

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
        pushed = 0
        for item in cfg.recurring:
            try:
                if await self._maybe_push(item, today):
                    pushed += 1
            except Exception as exc:
                # Une notif ratée ne doit pas bloquer les suivantes.
                log.warning(
                    "finance_cron_item_failed",
                    key=item.key,
                    error=str(exc),
                )
        log.info("finance_cron_run", pushed=pushed, today=today.isoformat())

    async def _maybe_push(self, item: RecurringItem, today: date) -> bool:
        """Pousse la notif si la récurrente est due aujourd'hui (ou en retard)."""
        effective_day = _clamp_day_to_month(item.day, today)
        # Le rappel est envoyé à partir du jour prévu (et tous les jours
        # suivants jusqu'au pointage). is_recurring_ticked_this_month
        # garantit qu'on n'écrit pas plusieurs lignes.
        if today.day < effective_day:
            return False
        if await self._expenses.is_recurring_ticked_this_month(item.key, today):
            return False

        text = _format_question(item, today)
        await self._notifications.add(
            text=text,
            title="💸 Récurrente",
            priority=0,
            sound="pushover",
        )
        log.info("finance_reminder_pushed", key=item.key, day=effective_day)
        return True


def _clamp_day_to_month(day: int, month: date) -> int:
    last = _calendar.monthrange(month.year, month.month)[1]
    return min(day, last)


def _format_question(item: RecurringItem, today: date) -> str:
    effective_day = _clamp_day_to_month(item.day, today)
    overdue = " (en retard)" if today.day > effective_day else ""
    amount = _format_amount_eur(item.amount_cents)
    verb = "versé" if item.kind == "saving" else "passé"
    return (
        f"{item.label} ({amount}, prévu le {effective_day}){overdue} — "
        f"déjà {verb} ce mois ? Réponds « {item.label.lower()} est {verb} » pour confirmer."
    )


def _format_amount_eur(cents: int) -> str:
    if cents % 100 == 0:
        return f"{cents // 100}€"
    euros = cents / 100
    return f"{euros:.2f}".replace(".", ",") + "€"
