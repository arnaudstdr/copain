# copain — Personal Telegram Assistant — CLAUDE.md

## Project overview

Single-user Telegram bot, entirely in natural French language. Designed for a
single user, partly self-hosted (local services on a Raspberry Pi 5 8 GB, main
LLM in the cloud).

### Current features

- **Conversation** with automatic semantic memory (ChromaDB + embeddings)
- **Tasks + reminders** in natural language (SQLite, Telegram push reminders
  at due time)
- **Web search** via self-hosted SearXNG with FR summary
- **RSS feeds**: add/list/remove + summary of the latest news on demand
- **Morning briefing** automatically every day (configurable time, default
  8am): local weather + today's tasks + today's events + top 5 summarised RSS
  items
- **Photo analysis**: Telegram image sent → LLM multimodal vision → routed
  through the normal pipeline (memory/task/event depending on content)
- **iCloud calendar** (CalDAV): event creation and listing in any iCloud
  calendar
- **Fuel prices**: via `data.economie.gouv.fr` open data API, top 5 stations
  around `HOME_CITY` (geocoding via OSM Nominatim)
- **Weather**: via Open-Meteo, supports FR expressions (`demain`, `ce
  weekend`, etc.) up to 16 days
- **Opt-in proactivity** (`PROACTIVITY_ENABLED=true`): rain alerts + event
  reminders with five safeguards (feature flag, time window, daily budget,
  dedup, cooldown). Disabled by default.

Everything flows through the same pipeline: an LLM decides the intent via a
`<meta>` JSON block, the code executes the side effects, then a text message
is returned to the user. Proactive notifications, on the other hand, run
through an autonomous job (no LLM, no `<meta>` routing) that writes into the
`notification_logs` table to track cooldowns and budget.

---

## Architecture

```text
Telegram API
     │
     ▼
Python bot (python-telegram-bot v21, async)
     │
     ├── Security middleware (ALLOWED_USER_ID whitelist)
     │
     ├── Handlers
     │     ├── filters.TEXT  → make_handler       → standard pipeline
     │     └── filters.PHOTO → make_photo_handler → pipeline + images[]
     │
     ├── LLM Client (Ollama — gemma4:31b-cloud multimodal)
     │     ├── call(system, user, images?)        → Ollama chat API
     │     ├── call_with_search(message, results) → re-run with SearXNG results
     │     └── chat(messages)                     → low-level call
     │
     ├── <meta> parser
     │     └── Intent ∈ {answer, task, search, memory, feed, event, fuel, weather}
     │         + TaskMeta / FeedMeta / EventMeta / FuelMeta / WeatherMeta
     │
     ├── Memory Manager (ChromaDB + nomic-embed-text via Ollama)
     │     ├── store()             → embed + persist the memory_content
     │     └── retrieve_context()  → top-5 relevant chunks
     │
     ├── Task Manager (SQLite via SQLAlchemy async + aiosqlite)
     │     ├── create / list_pending / complete / delete
     │     └── ReminderScheduler
     │           ├── SQLAlchemyJobStore → persisted one-shot reminders
     │           └── MemoryJobStore     → cron (non-serialisable closures)
     │
     ├── RSS Manager
     │     ├── FeedManager (SQLAlchemy, table `feeds`)
     │     └── RssFetcher (feedparser via asyncio.to_thread)
     │
     ├── Search Manager
     │     └── SearxngClient (local HTTP JSON)
     │
     ├── iCloud Calendar (CalDAV via `caldav` lib)
     │     ├── ICloudCalendarClient.connect()    → calendar discovery
     │     ├── create_event(calendar_name?)      → fuzzy match of the target calendar
     │     └── list_between / list_today / list_upcoming
     │
     ├── Fuel (open data fuel prices)
     │     ├── FuelClient         → data.economie.gouv.fr (ODS v2.1)
     │     └── NominatimClient    → OSM geocoding (FR, in-memory cache)
     │
     └── Briefing Service (APScheduler cron job)
           ├── OpenMeteoClient (Sélestat weather)
           ├── _today_tasks / _today_events / _rss_block
           └── send_daily → Telegram push at the configured time
```

---

## Tech stack

| Component     | Choice                                                  |
| ------------- | ------------------------------------------------------- |
| Language      | Python 3.12+ async/await everywhere                     |
| Telegram bot  | `python-telegram-bot >= 21`                             |
| LLM           | Ollama → `gemma4:31b-cloud` (multimodal, cloud)         |
| Embeddings    | Ollama → `nomic-embed-text` (local)                     |
| Vector memory | ChromaDB (local persistence)                            |
| ORM           | SQLAlchemy 2 async + aiosqlite                          |
| Scheduler     | APScheduler (SQLAlchemy + Memory jobstores)             |
| Dates         | dateparser (FR) + noon/midnight normalisation           |
| RSS           | feedparser                                              |
| Weather       | Open-Meteo (HTTP, no key)                               |
| Calendar      | CalDAV via `caldav` + `vobject`                         |
| Web search    | SearXNG (local Docker instance)                         |
| Fuel prices   | data.economie.gouv.fr (Opendatasoft v2.1, no key)       |
| Geocoding     | Nominatim OSM (HTTP, no key, in-memory cache)           |
| Logs          | structlog (console in dev, JSON in prod)                |
| Container     | Docker + Docker Compose                                 |
| Tests         | pytest + pytest-asyncio (auto mode)                     |
| Quality       | ruff (lint+format) + mypy strict via pre-commit         |

---

## System prompt (routing via the `<meta>` block)

On every call, the LLM receives a system prompt whose centrepiece is a
`<meta>` JSON block that it MUST include at the end of every reply:

```json
{
  "intent": "answer|task|search|memory|feed|event|fuel|weather",
  "store_memory": true|false,
  "memory_content": "factual summary if store_memory=true, otherwise null",
  "task": {
    "content": "description if intent=task, otherwise null",
    "due_str": "FR expression if a due date is mentioned, otherwise null"
  },
  "feed": {
    "action": "add|list|remove|summarize, otherwise null",
    "name": "feed name, otherwise null",
    "url": "URL if action=add, otherwise null"
  },
  "event": {
    "action": "create|list, otherwise null",
    "title": "title if action=create, otherwise null",
    "start_str": "FR expression (e.g. 'demain midi'), otherwise null",
    "end_str": "FR expression if an end is given, otherwise null (default duration 1h)",
    "location": "location if mentioned, otherwise null",
    "description": "note, otherwise null",
    "range_str": "range if action=list (e.g. 'cette semaine'), otherwise null",
    "calendar_name": "target calendar if specified (fuzzy match), otherwise null"
  },
  "fuel": {
    "fuel_type": "gazole|sp95|sp98|e10|e85|gplc if intent=fuel, otherwise null",
    "radius_km": "number if a radius is mentioned (e.g. 'dans 5 km'), otherwise null",
    "location": "city or place if specified, otherwise null (= around HOME_CITY)"
  },
  "weather": {
    "location": "city or place if specified, otherwise null (= HOME_CITY)",
    "when": "FR expression if specified (e.g. 'demain', 'ce weekend'), otherwise null (= today)"
  },
  "search_query": "query if intent=search, otherwise null"
}
```

The full prompt template lives in `bot/llm/prompt.py`. Two critical routing
rules: appointments with a time go to `event` (not `task`), and the LLM
copies literal `midi` / `minuit` that the code normalises to `12:00` /
`00:00` before `dateparser.parse`.

---

## Security

**The bot only replies to a single user.** Strictly checked on every update
in all handlers:

```python
if not is_allowed(update, deps.settings.allowed_user_id):
    return  # silent, warning logged
```

Unauthorised access attempts are logged with `user_id` + `username`.

---

## Path-scoped rules

Detailed guidance lives in `.claude/rules/` and is loaded only when Claude
reads files matching the rule's `paths` pattern:

| Rule file               | Loaded on                                                         |
| ----------------------- | ----------------------------------------------------------------- |
| `python-conventions.md` | `bot/**/*.py`, `tests/**/*.py`                                    |
| `project-structure.md`  | `bot/**/*.py`, `tests/**/*.py`                                    |
| `config-env.md`         | `bot/config.py`, `.env*`, `docker-compose.yml`, `Dockerfile`      |
| `handlers.md`           | `bot/handlers.py`, `bot/main.py`                                  |
| `llm.md`                | `bot/llm/**`                                                      |
| `calendar.md`           | `bot/calendar/**`                                                 |
| `scheduler.md`          | `bot/tasks/scheduler.py`, `bot/briefing/**`, `bot/proactivity/**` |
