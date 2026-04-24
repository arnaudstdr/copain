---
paths:
  - "bot/**/*.py"
  - "tests/**/*.py"
---

# Project structure

```
copain/
├── CLAUDE.md                    # project source of truth (short, always loaded)
├── README.md                    # user-facing setup
├── .claude/
│   └── rules/                   # path-scoped rules (loaded on matching files)
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
│   ├── sentry_setup.py          # opt-in Sentry init + capture_exception helper
│   ├── cache.py                 # TTLCache (LRU async) — LLM opt-in + SearXNG
│   ├── db.py                    # AsyncEngine partagé + WAL mode
│   ├── http_retry.py            # httpx retry + JSON helper (Open-Meteo, ODS, …)
│   ├── telegram_sender.py       # send_message + TelegramStreamSink + visible_text
│   │
│   ├── llm/
│   │   ├── client.py            # LLMClient (chat + chat_stream + fallback + cache)
│   │   ├── prompt.py            # SYSTEM_PROMPT_TEMPLATE + build_system_prompt
│   │   └── parser.py            # Meta TypedDict + extract_meta
│   │
│   ├── memory/
│   │   ├── manager.py           # MemoryManager (ChromaDB HNSW + store_many)
│   │   └── embeddings.py        # Embedder (nomic-embed-text, embed_many async)
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
    ├── test_briefing.py
    ├── test_cache.py
    ├── test_calendar.py
    ├── test_config.py
    ├── test_embedder.py
    ├── test_feeds.py
    ├── test_fuel_client.py
    ├── test_handlers_dates.py
    ├── test_handlers_process.py
    ├── test_http_retry.py
    ├── test_llm_client.py
    ├── test_logging_conf.py
    ├── test_memory.py
    ├── test_nominatim.py
    ├── test_parser.py
    ├── test_proactivity_models.py
    ├── test_proactivity_rules.py
    ├── test_proactivity_service.py
    ├── test_scheduler_interval.py
    ├── test_scheduler_security.py
    ├── test_searxng_cache.py
    ├── test_security.py
    ├── test_sentry.py
    ├── test_streaming.py
    ├── test_tasks.py
    └── test_weather.py
```
