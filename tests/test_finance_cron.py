"""Tests du cron `FinanceReminderJob` (mocks complets, pas d'engine)."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from bot.finance.cron import FinanceReminderJob
from bot.profile import UserProfile


def _profile(items: list[dict[str, object]]) -> UserProfile:
    return UserProfile(
        raw_yaml="",
        is_loaded=True,
        data={"finances": {"currency": "EUR", "recurring": items}},
    )


def _civil_bounds(today: date) -> tuple[date, date]:
    """Bornes mois civil (= fallback du cycle quand aucune ancre n'existe)."""
    start = today.replace(day=1)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def _build_job(
    *,
    profile: UserProfile,
    ticked: bool = False,
) -> tuple[FinanceReminderJob, MagicMock, MagicMock]:
    expenses = MagicMock()
    expenses.is_recurring_ticked_in_cycle = AsyncMock(return_value=ticked)
    expenses.current_cycle_bounds = AsyncMock(side_effect=_civil_bounds)
    notifications = MagicMock()
    notifications.add = AsyncMock()
    job = FinanceReminderJob(
        profile=profile,
        expenses=expenses,
        notifications=notifications,
        timezone="Europe/Paris",
    )
    return job, expenses, notifications


def _patch_today(day: date):  # type: ignore[no-untyped-def]
    """Patch datetime.now du module cron pour renvoyer un jour fixe."""
    fake = datetime(day.year, day.month, day.day, 9, 0)
    return patch("bot.finance.cron.datetime", MagicMock(now=MagicMock(return_value=fake)))


async def test_run_silent_when_no_finance_section() -> None:
    job, _expenses, notifications = _build_job(profile=UserProfile(raw_yaml="", is_loaded=False))
    await job.run()
    notifications.add.assert_not_called()


async def test_run_silent_when_due_day_not_yet_reached() -> None:
    profile = _profile(
        [{"key": "netflix", "label": "Netflix", "amount": 17.99, "day": 12, "kind": "expense"}]
    )
    job, _expenses, notifications = _build_job(profile=profile)
    with _patch_today(date(2026, 5, 5)):
        await job.run()
    notifications.add.assert_not_called()


async def test_run_pushes_notif_for_due_today() -> None:
    profile = _profile(
        [{"key": "loyer", "label": "Loyer", "amount": 800, "day": 5, "kind": "expense"}]
    )
    job, _expenses, notifications = _build_job(profile=profile)
    with _patch_today(date(2026, 5, 5)):
        await job.run()
    notifications.add.assert_awaited_once()
    kwargs = notifications.add.await_args.kwargs
    assert "Loyer" in kwargs["text"]
    assert kwargs["title"] == "💸 Récurrente"


async def test_run_pushes_for_overdue_unticked() -> None:
    profile = _profile(
        [{"key": "loyer", "label": "Loyer", "amount": 800, "day": 5, "kind": "expense"}]
    )
    job, _expenses, notifications = _build_job(profile=profile)
    with _patch_today(date(2026, 5, 10)):
        await job.run()
    notifications.add.assert_awaited_once()
    text = notifications.add.await_args.kwargs["text"]
    assert "en retard" in text


async def test_run_silent_when_already_ticked() -> None:
    profile = _profile(
        [{"key": "loyer", "label": "Loyer", "amount": 800, "day": 5, "kind": "expense"}]
    )
    job, _expenses, notifications = _build_job(profile=profile, ticked=True)
    with _patch_today(date(2026, 5, 5)):
        await job.run()
    notifications.add.assert_not_called()


async def test_run_day_31_caps_to_last_day_of_short_month() -> None:
    profile = _profile(
        [{"key": "fin_mois", "label": "Fin de mois", "amount": 50, "day": 31, "kind": "expense"}]
    )
    job, _expenses, notifications = _build_job(profile=profile)
    # Février 2026 = 28 jours, on tick le job le 28.
    with _patch_today(date(2026, 2, 28)):
        await job.run()
    notifications.add.assert_awaited_once()


async def test_run_continues_after_one_item_fails() -> None:
    profile = _profile(
        [
            {"key": "loyer", "label": "Loyer", "amount": 800, "day": 5, "kind": "expense"},
            {"key": "netflix", "label": "Netflix", "amount": 17.99, "day": 5, "kind": "expense"},
        ]
    )
    job, expenses, notifications = _build_job(profile=profile)

    async def maybe_raise(key: str, _today: date) -> bool:
        if key == "loyer":
            raise RuntimeError("flaky")
        return False

    expenses.is_recurring_ticked_in_cycle = AsyncMock(side_effect=maybe_raise)
    with _patch_today(date(2026, 5, 5)):
        await job.run()
    # Netflix doit avoir été notifié malgré la panne du loyer.
    notifications.add.assert_awaited_once()
    text = notifications.add.await_args.kwargs["text"]
    assert "Netflix" in text


async def test_run_saving_uses_verse() -> None:
    profile = _profile([{"key": "pel", "label": "PEL", "amount": 200, "day": 5, "kind": "saving"}])
    job, _expenses, notifications = _build_job(profile=profile)
    with _patch_today(date(2026, 5, 5)):
        await job.run()
    text = notifications.add.await_args.kwargs["text"]
    assert "versé" in text
