---
paths:
  - "bot/tasks/scheduler.py"
  - "bot/finance/cron.py"
  - "bot/proactivity/**"
---

# Scheduler, finance reminder and proactivity

## APScheduler — two jobstores

`ReminderScheduler` configures two stores:

- **`default` (SQLAlchemyJobStore)** — one-shot task reminders persisted
  across restarts (`add_reminder(task_id, due_at, content)`). At due time
  the job executes `_send_reminder(db_path, content, pushover_token, pushover_user)`,
  which builds a `NotificationStore` + `PushoverClient` from the serialised
  primitives and enqueues the reminder. No live object is ever pickled — only
  primitives. Jobs persisted before the Pushover migration use default empty
  values for token/user (= no push, silent backwards compat).
- **`memory` (MemoryJobStore)** — recurring jobs whose function is a
  non-serialisable closure (e.g. finance reminder, proactivity tick). They
  are re-scheduled at startup via:
  - `add_cron_job(job_id, func, hour, minute)` for daily cron jobs
    (finance reminder).
  - `add_interval_job(job_id, func, minutes)` for "every N minutes" jobs
    (proactivity tick).

Both honour the configured timezone (`settings.timezone`). Never serialise
closures into `default` — they will fail to re-hydrate after a restart.

### Error capture to Sentry

An `EVENT_JOB_ERROR` listener (`_on_job_error`) logs any job exception
(`job_error job_id=… error=…`) and forwards it to Sentry via
`sentry_setup.capture_exception(exc, source="apscheduler", job_id=…)`.
Covers reminders, finance reminder, proactivity tick — no wrapping
`try/except` needed in the job bodies.

## Finance reminder

`FinanceReminderJob.run` (`bot/finance/cron.py`) runs as a daily cron job
at `FINANCE_REMINDER_HOUR:FINANCE_REMINDER_MINUTE`. For each recurring item
of the YAML profile due in the current budget cycle and not yet ticked
(`is_recurring_ticked_in_cycle`), it enqueues a question via
`NotificationStore.add(..., title="💸 Récurrente")`. The closure is
registered in the `memory` jobstore at startup (`bot/main.py`).

The historical morning briefing (`BriefingService`) was intentionally
removed (no unsolicited pushes); `bot/weather/` only hosts the Open-Meteo
client today.

## NotificationStore — double canal

`NotificationStore.add(text, title, priority, sound)` écrit simultanément :
1. SQLite `pending_notifications` (consommé par `GET /notifications`)
2. Pushover via `PushoverClient.push()` — fail silently si non configuré

`PushoverClient` est injecté dans le constructeur de `NotificationStore`. Si token
ou user est vide, aucun appel réseau n'est tenté. Les deux canaux coexistent toujours.

## Proactivity (opt-in)

`ProactivityService.tick` runs every `PROACTIVITY_CHECK_INTERVAL_MIN`
minutes (default 30) and may push **at most one** notification per tick.
Two rules in v1: rain alert within the hour (Open-Meteo hourly) and
appointment reminder ~1 h before (iCloud).

The service accepts either a `NotificationStore` (production — uses `add()`
with title/priority/sound from the `Notification` dataclass) or a `send`
callable (`Callable[[str], Awaitable[None]]`) used by tests with an
`AsyncMock` (text only). Each `Notification` carries its own `title`,
`priority`, and `sound` set by the rule function (`evaluate_rain`,
`evaluate_upcoming_event`).

Pushover priorities used:
- Rain alert: `priority=0`, `sound="rain"`, `title="🌧️ Alerte pluie"`
- Event reminder: `priority=1` (bypasses silent mode), `sound="magic"`, `title="📅 Rappel RDV"`
- Task reminder: `priority=1`, `sound="pushover"`, `title="Rappel"`
- Finance reminder: `priority=0`, `sound="pushover"`, `title="💸 Récurrente"`

Five safeguards to preserve when editing `tick` or the rules:

1. Global feature flag (`PROACTIVITY_ENABLED`, disabled by default).
2. Configurable time window (defaults 8am-9pm).
3. Daily budget cap (default 3 notifications/day).
4. Dedup by `event_uid` for event reminders (via `notification_logs`
   table).
5. Temporal cooldown for rain (`PROACTIVITY_RAIN_COOLDOWN_HOURS`).

`notification_logs` lives in `tasks.db` and shares the SQLAlchemy `Base`
from `bot.tasks.models` (alongside `tasks`, `feeds`, `pending_notifications`).
Rules in `bot/proactivity/rules.py` are pure functions — side effects
(logging, push) belong in the service.
