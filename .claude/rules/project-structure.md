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
│   ├── main.py                  # entrypoint: build deps + launch uvicorn
│   ├── api.py                   # FastAPI app + endpoints + verify_api_key dep
│   ├── pipeline.py              # process_message + _handle_* (transport-agnostic)
│   ├── config.py                # Settings dataclass + load_settings()
│   ├── logging_conf.py          # structlog setup
│   ├── sentry_setup.py          # opt-in Sentry init + capture_exception helper
│   ├── cache.py                 # TTLCache (LRU async) — LLM opt-in + SearXNG
│   ├── db.py                    # AsyncEngine partagé + WAL mode
│   ├── http_retry.py            # httpx retry + JSON helper (Open-Meteo, ODS, …)
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
│   ├── notifications/
│   │   ├── models.py            # PendingNotification (shares Base with tasks)
│   │   └── store.py             # NotificationStore (add / get_unread / mark_read)
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
│   ├── tasks.db                 # SQLite : tasks + feeds + notification_logs + pending_notifications
│   └── scheduler.db             # persisted APScheduler jobs
│
└── tests/                       # pytest-asyncio, everything mocked (no external I/O)
    ├── conftest.py
    ├── test_api.py
    ├── test_briefing.py
    ├── test_cache.py
    ├── test_calendar.py
    ├── test_config.py
    ├── test_embedder.py
    ├── test_feeds.py
    ├── test_fuel_client.py
    ├── test_http_retry.py
    ├── test_llm_client.py
    ├── test_logging_conf.py
    ├── test_memory.py
    ├── test_nominatim.py
    ├── test_notifications_store.py
    ├── test_parser.py
    ├── test_pipeline_dates.py
    ├── test_pipeline_process.py
    ├── test_proactivity_models.py
    ├── test_proactivity_rules.py
    ├── test_proactivity_service.py
    ├── test_scheduler_interval.py
    ├── test_searxng_cache.py
    ├── test_sentry.py
    ├── test_tasks.py
    └── test_weather.py
```
