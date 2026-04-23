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
- **Photo analysis**: Telegram image sent → LLM multimodal vision → text
  extraction, scene description, chart understanding, then routed through the
  normal pipeline (memory/task/event depending on content)
- **iCloud calendar** (CalDAV): event creation and listing in any iCloud
  calendar, natively visible on iPhone / Apple Watch / Mac
- **Fuel prices**: via the `data.economie.gouv.fr` open data API (dataset
  `prix-des-carburants-en-france-flux-instantane-v2`, no key required). Ask
  in natural language "gazole pas cher ?" → top 5 stations sorted by price
  within a 10 km radius around `HOME_CITY`. Support for named locations via
  OSM Nominatim geocoding ("SP98 à Colmar dans 5 km"). FR synonyms mapped
  (diesel → gazole, 98 → sp98, etc.).
- **Weather**: via Open-Meteo (no key required), dedicated source to avoid
  the SearXNG fallback. Ask "quel temps fait-il ?" → today at `HOME_CITY`;
  "météo à Strasbourg ce weekend" → multi-day forecast for a location
  geocoded via Nominatim. Supported FR expressions: `aujourd'hui`, `demain`,
  `après-demain`, `ce weekend`, `cette semaine`, `dans N jours`, fallback to
  `dateparser` for the rest. 16-day limit (Open-Meteo).
- **Opt-in proactivity**: an APScheduler job ticks every N min (default 30)
  and may push at most **one** notification per tick. Two rules in v1 — rain
  alert within the hour (Open-Meteo hourly) and appointment reminder ~1 h
  before (iCloud calendar). Five safeguards prevent spam: global feature
  flag, configurable time window (default 8am-9pm), daily budget capped at
  3, dedup by `event_uid` for events, temporal cooldown for rain. Disabled
  by default (`PROACTIVITY_ENABLED=false`).

Everything flows through the same pipeline: an LLM decides the intent via a
`<meta>` JSON block, the code executes the side effects, then a text message
is returned to the user. Proactive notifications, on the other hand, run
through an autonomous job (no LLM, no `<meta>` routing) that writes into the
`notification_logs` table to track cooldowns and budget.

---

## Architecture

```
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

| Component          | Choice                                                   |
|--------------------|----------------------------------------------------------|
| Language           | Python 3.12+ async/await everywhere                      |
| Telegram bot       | `python-telegram-bot >= 21`                              |
| LLM                | Ollama → `gemma4:31b-cloud` (multimodal, cloud)          |
| Embeddings         | Ollama → `nomic-embed-text` (local)                      |
| Vector memory      | ChromaDB (local persistence)                             |
| ORM                | SQLAlchemy 2 async + aiosqlite                           |
| Scheduler          | APScheduler (SQLAlchemy + Memory jobstores)              |
| Dates              | dateparser (FR) + noon/midnight normalisation            |
| RSS                | feedparser                                               |
| Weather            | Open-Meteo (HTTP, no key)                                |
| Calendar           | CalDAV via `caldav` + `vobject`                          |
| Web search         | SearXNG (local Docker instance)                          |
| Fuel prices        | data.economie.gouv.fr (Opendatasoft v2.1, no key)        |
| Geocoding          | Nominatim OSM (HTTP, no key, in-memory cache)            |
| Config             | python-dotenv (.env validated by a Settings dataclass)   |
| Logs               | structlog (console in dev, JSON in prod)                 |
| Container          | Docker + Docker Compose                                  |
| Tests              | pytest + pytest-asyncio (auto mode)                      |
| Quality            | ruff (lint+format) + mypy strict via pre-commit          |

---

## Models

| Model                 | Type        | Where it runs    | Usage                                        |
|-----------------------|-------------|------------------|----------------------------------------------|
| `gemma4:31b-cloud`    | Vision LLM  | Ollama Cloud     | All replies + image analysis                 |
| `nomic-embed-text`    | Embeddings  | Ollama local Pi  | Vectors for ChromaDB (on demand)             |

The LLM was moved to the cloud because local inference on the Pi 5 was too
slow. As a result, most of the initial RAM budget is freed. Only
`nomic-embed-text` (~300 MB on demand), ChromaDB/bot (~1 GB), and SearXNG
(~0.3 GB) run locally on the Pi.

---

## Project structure

```
copain/
├── CLAUDE.md                    # this file (project source of truth)
├── README.md                    # user-facing setup
├── .env                         # secrets (never committed)
├── .env.example
├── docker-compose.yml
├── Dockerfile
├── Makefile                     # install/run/test/lint/typecheck/docker-*
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml               # ruff + mypy + pytest config
├── .pre-commit-config.yaml
│
├── bot/
│   ├── __init__.py
│   ├── main.py                  # entrypoint + PTB post_init/post_shutdown
│   ├── handlers.py              # make_handler + make_photo_handler + _handle_*
│   ├── security.py              # is_allowed(update, allowed_user_id)
│   ├── config.py                # Settings dataclass + load_settings()
│   ├── logging_conf.py          # structlog setup
│   │
│   ├── llm/
│   │   ├── client.py            # LLMClient (chat + base64 images)
│   │   ├── prompt.py            # SYSTEM_PROMPT_TEMPLATE + build_system_prompt
│   │   └── parser.py            # Meta TypedDict + extract_meta
│   │
│   ├── memory/
│   │   ├── manager.py           # MemoryManager (ChromaDB)
│   │   └── embeddings.py        # Embedder (nomic-embed-text)
│   │
│   ├── tasks/
│   │   ├── manager.py           # TaskManager async
│   │   ├── models.py            # Task + Base DeclarativeBase (shared)
│   │   └── scheduler.py         # ReminderScheduler (add_reminder + add_cron_job)
│   │
│   ├── rss/
│   │   ├── manager.py           # FeedManager CRUD
│   │   ├── models.py            # Feed (shares Base with Task)
│   │   └── fetcher.py           # RssFetcher via asyncio.to_thread
│   │
│   ├── search/
│   │   └── searxng.py           # SearxngClient
│   │
│   ├── calendar/
│   │   ├── models.py            # CalendarEvent dataclass
│   │   └── client.py            # ICloudCalendarClient (connect + fuzzy match)
│   │
│   ├── briefing/
│   │   ├── weather.py           # OpenMeteoClient + HourlyPrecipitation + FR codes
│   │   └── service.py           # BriefingService (aggregates + cron)
│   │
│   ├── fuel/
│   │   ├── models.py            # FuelType + FuelStation + GeoPoint + FR synonyms
│   │   ├── client.py            # FuelClient (data.economie.gouv.fr ODS v2.1)
│   │   └── geocoding.py         # NominatimClient (OSM FR + in-memory cache)
│   │
│   └── proactivity/
│       ├── models.py            # NotificationLog (shares Base with tasks)
│       ├── rules.py             # evaluate_rain + evaluate_upcoming_event (pure)
│       └── service.py           # ProactivityService.tick + safeguards
│
├── data/                        # persisted Docker volume
│   ├── chroma/
│   ├── tasks.db                 # SQLite shared by tasks + feeds + notification_logs
│   └── scheduler.db             # persisted APScheduler jobs
│
└── tests/                       # pytest-asyncio, everything mocked (no external I/O)
    ├── conftest.py
    ├── test_parser.py
    ├── test_llm_client.py
    ├── test_tasks.py
    ├── test_memory.py
    ├── test_security.py
    ├── test_feeds.py
    ├── test_briefing.py
    ├── test_weather.py
    ├── test_calendar.py
    ├── test_config.py
    ├── test_handlers_dates.py
    ├── test_scheduler_interval.py
    ├── test_proactivity_models.py
    ├── test_proactivity_rules.py
    ├── test_proactivity_service.py
    ├── test_fuel_client.py
    └── test_nominatim.py
```

---

## Environment variables (.env)

```env
# Telegram
TELEGRAM_BOT_TOKEN=                  # required
ALLOWED_USER_ID=                     # required, your Telegram user_id only

# Ollama (client + models)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_LLM_MODEL=gemma4:31b-cloud
OLLAMA_EMBED_MODEL=nomic-embed-text

# SearXNG (local instance)
SEARXNG_BASE_URL=http://localhost:8888

# Data paths (mounted as a Docker volume)
DATA_DIR=./data
CHROMA_DIR=./data/chroma
DB_PATH=./data/tasks.db
SCHEDULER_DB_PATH=./data/scheduler.db

# Timezone (dateparser + APScheduler + display)
TZ=Europe/Paris

# Scheduled morning briefing
BRIEFING_HOUR=8
BRIEFING_MINUTE=0

# Coordinates for weather (Open-Meteo, no API key)
HOME_LAT=48.26
HOME_LON=7.45
HOME_CITY=Sélestat

# iCloud Calendar (CalDAV) — App-Specific Password required (Apple ID 2FA)
# Generate at: https://appleid.apple.com → Sign-In and Security
ICLOUD_USERNAME=                     # required, Apple ID
ICLOUD_APP_PASSWORD=                 # required, format xxxx-xxxx-xxxx-xxxx
ICLOUD_CALENDAR_NAME=Personnel       # default calendar name (fuzzy match)

# Proactivity (notifications pushed without a request — strict opt-in)
PROACTIVITY_ENABLED=false
PROACTIVITY_WINDOW_START_HOUR=8
PROACTIVITY_WINDOW_END_HOUR=21
PROACTIVITY_DAILY_BUDGET=3
PROACTIVITY_CHECK_INTERVAL_MIN=30
PROACTIVITY_RAIN_COOLDOWN_HOURS=3

# Fuel prices (data.economie.gouv.fr — no API key)
FUEL_DEFAULT_RADIUS_KM=10              # default search radius
# User-Agent required by the Nominatim policy:
# https://operations.osmfoundation.org/policies/nominatim/
NOMINATIM_USER_AGENT=copain-bot/1.0 (personal assistant)

# Logs — rotating JSON file persisted in the Docker volume (5 MB x 5 backups).
# Leave empty to disable file persistence (stdout stays active).
LOG_FILE_PATH=./data/logs/bot.log

# Environment (dev | prod) — controls the structlog log format
ENV=dev
```

`bot/config.py` loads and validates these variables. Variables marked as
"required" crash the startup if missing (explicit `ConfigError`).

---

## System Prompt (routing via the `<meta>` block)

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

The full prompt lives in `bot/llm/prompt.py` (`SYSTEM_PROMPT_TEMPLATE`). It
contains 8 few-shot examples to stabilise gemma4's routing (feed, event,
fuel). Two critical rules are written there:

- **Task vs event distinction**: appointment/meeting WITH a time → `event`,
  otherwise → `task`.
- **Temporal words**: the LLM copies the literal words `midi` / `minuit`
  into `start_str`; normalisation on the code side
  (`_normalize_fr_time_words`) converts them to `12:00` / `00:00` before
  `dateparser.parse`.

---

## Message processing flow

```python
async def _process(user_text, chat_id, deps, images=None) -> str:
    # 1. Contextual memory (top-5 via embeddings)
    memory_context = await deps.memory.retrieve_context(user_text)

    # 2. Build the system prompt (memory + history)
    system = build_system_prompt(memory_context, deps.history)

    # 3. Call the LLM (+ optional base64 images)
    raw = await deps.llm.call(system=system, user=user_text, images=images)

    # 4. Extract the <meta> block + clean text
    text, meta = extract_meta(raw)

    # 5. Side effects depending on the intent
    await _apply_side_effects(user_text, chat_id, meta, deps)
    # → store memory, create task + reminder scheduler

    # 6. Branches that re-run the LLM or replace the text
    if meta["intent"] == "search" and meta["search_query"]:
        results = await deps.search.search(...)
        text = await deps.llm.call_with_search(user_text, results)
    elif meta["intent"] == "feed" and meta["feed"]["action"]:
        text = await _handle_feed(...)   # add/list/remove/summarize
    elif meta["intent"] == "event" and meta["event"]["action"]:
        text = await _handle_event(...)  # create (iCloud) / list
    elif meta["intent"] == "fuel" and meta["fuel"]["fuel_type"]:
        text = await _handle_fuel(...)   # fuel prices + geocoding
    elif meta["intent"] == "weather":
        text = await _handle_weather(...) # Open-Meteo + geocoding + day range

    # 7. Rolling history + return
    deps.history.extend([f"user: {user_text}", f"assistant: {text}"])
    return text
```

For photos, `make_photo_handler` downloads the Telegram blob and calls the
same `_process()` with `images=[bytes]`. The multimodal LLM handles caption
+ image in a single call.

---

## Scheduler (APScheduler)

Two jobstores configured in `ReminderScheduler`:

- **`default` (SQLAlchemyJobStore)** — one-shot task reminders persisted
  across restarts (`add_reminder(task_id, due_at, chat_id, content)`).
- **`memory` (MemoryJobStore)** — recurring jobs (cron) whose function is a
  non-serialisable closure (e.g. briefing). They are re-scheduled at startup
  via `add_cron_job(job_id, func, hour, minute)` in `_post_init`.

Both honour the configured timezone (`settings.timezone`).

---

## iCloud calendar

`ICloudCalendarClient` connects to `https://caldav.icloud.com/` via the
`caldav` library (synchronous, wrapped in `asyncio.to_thread`). Auth via an
App-Specific Password.

On `connect()`, all available calendars are listed and stored in
`self._all_calendars`. The `resolve_calendar(name)` method performs a
3-level tolerant match:

1. Exact match
2. Normalised match (NFC + strip ZWJ `‍` + variation selectors `️` + trim +
   casefold)
3. "Contains" match on the alphanumeric-only version

Consequence: the user can write `ICLOUD_CALENDAR_NAME=Personnel` or ask "in
the sport calendar" even if the real names are `🧘‍♂️ Personnel ` and
`🚴‍♂️ Sport ` with emojis + spaces.

Current scope: **create + list**. No delete/modification/recurrence.

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

## Docker Compose

```yaml
services:
  bot:
    build: .
    restart: unless-stopped
    env_file: .env
    network_mode: host          # direct access to local Ollama on localhost
    volumes:
      - ./data:/app/data

  searxng:
    image: searxng/searxng:latest
    restart: unless-stopped
    ports:
      - "8888:8080"
    volumes:
      - ./searxng:/etc/searxng
```

Ollama runs **outside Docker** on the Pi (GPU/NPU ARM access); it targets
the `cloud` model when the model ID contains the `-cloud` suffix.

---

## Global error handling

- Telegram handlers wrap each call in `try/except` and respond with a
  generic message on internal errors.
- An `Application.add_error_handler(_error_handler)` is registered to
  soft-fail on `NetworkError` / `TimedOut` (momentary dropouts to
  `api.telegram.org`), which are logged as warnings without stacktrace. Any
  other error goes through `log.exception`.
- `post_init` tolerates a failure of `ICloudCalendarClient.connect()`: it
  logs a warning and lets the bot start. The `event` intent will then return
  "calendar unavailable".

---

## Hardware constraints (Pi 5 8 GB)

With the LLM in the cloud, local RAM pressure is low:

| Component             | RAM       |
|-----------------------|-----------|
| ChromaDB + Python bot | ~1 GB     |
| SearXNG               | ~0.3 GB   |
| nomic-embed-text      | ~0.3 GB on demand |
| **Total**             | **~1.6 GB out of 7 GB available** |

Plenty of headroom to add other services (Home Assistant, RSSHub, etc.) if
needed in the future.

---

## Code conventions

- **Python 3.12+**, strict type hints everywhere (`mypy --strict`)
- **async/await** for all I/O (Telegram, Ollama, ChromaDB, SQLite, httpx,
  caldav in `to_thread`)
- **Explicit error handling**, no `bare except`
- **Structured logs via `structlog`** (not the standard `logging`), two
  handlers: stdout (coloured console in dev, JSON in prod depending on
  `ENV`) + rotating JSON file `data/logs/bot.log` (5 MB x 5 backups) if
  `LOG_FILE_PATH` is set — persisted in the Docker volume for `grep`/`jq`
  after the fact
- **Environment variables via `python-dotenv`**, never hardcoded values
  outside of `bot/config.py`
- **Tests with pytest + pytest-asyncio** (auto mode), external dependencies
  mocked — the suite runs without real Ollama/Telegram/iCloud/SearXNG
- **Pre-commit** with `ruff check --fix`, `ruff format`, `mypy --strict` on
  `bot/`
- **Shared SQLAlchemy Base** between modules that live together in
  `tasks.db` (tasks + feeds) — import `Base` from `bot.tasks.models`

Useful commands via the `Makefile`:

```bash
make install   # creates .venv, installs dev deps, installs pre-commit hooks
make run       # runs the bot in foreground
make test      # pytest (no external I/O)
make lint      # ruff check
make format    # ruff format + --fix
make typecheck # mypy strict
make docker-build / docker-up / docker-down
```
