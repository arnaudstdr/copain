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
