---
paths:
  - "bot/tasks/scheduler.py"
  - "bot/briefing/**"
  - "bot/proactivity/**"
---

# Scheduler, briefing and proactivity

## APScheduler — two jobstores

`ReminderScheduler` configures two stores:

- **`default` (SQLAlchemyJobStore)** — one-shot task reminders persisted
  across restarts (`add_reminder(task_id, due_at, content)`). At due time
  the job executes `_send_reminder(db_path, content)`, which builds a
  `NotificationStore` from the serialised path and enqueues a row into
  `pending_notifications`. No live object (engine, store) is ever pickled
  into the job — only primitives.
- **`memory` (MemoryJobStore)** — recurring jobs whose function is a
  non-serialisable closure (e.g. briefing, proactivity tick). They are
  re-scheduled at startup via:
  - `add_cron_job(job_id, func, hour, minute)` for daily cron jobs (briefing).
  - `add_interval_job(job_id, func, minutes)` for "every N minutes" jobs
    (proactivity tick).

Both honour the configured timezone (`settings.timezone`). Never serialise
closures into `default` — they will fail to re-hydrate after a restart.

### Error capture to Sentry

An `EVENT_JOB_ERROR` listener (`_on_job_error`) logs any job exception
(`job_error job_id=… error=…`) and forwards it to Sentry via
`sentry_setup.capture_exception(exc, source="apscheduler", job_id=…)`.
Covers reminders, briefing, proactivity tick — no wrapping `try/except`
needed in the job bodies.

## Briefing

`BriefingService.send_daily` runs as a cron job at `BRIEFING_HOUR:BRIEFING_MINUTE`
and aggregates: local weather (Open-Meteo), today's tasks, today's events
(iCloud), top 5 RSS summaries. The final string is enqueued via
`NotificationStore.add(text)`. The closure is registered in the `memory`
jobstore at startup (`bot/main.py::_build_state`).

## Proactivity (opt-in)

`ProactivityService.tick` runs every `PROACTIVITY_CHECK_INTERVAL_MIN`
minutes (default 30) and may push **at most one** notification per tick.
Two rules in v1: rain alert within the hour (Open-Meteo hourly) and
appointment reminder ~1 h before (iCloud).

The service accepts either a `NotificationStore` (production) or a `send`
callable (`Callable[[str], Awaitable[None]]`) used by tests with an
`AsyncMock`. The signature is `send(text)` — no chat_id since the API era.

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
