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
├── requirements.lock            # pip freeze de référence (traçabilité)
├── pyproject.toml               # ruff + mypy + pytest config
├── .pre-commit-config.yaml
│
├── bot/
│   ├── __init__.py
│   ├── main.py                  # entrypoint: build deps + launch uvicorn
│   ├── api.py                   # FastAPI app + endpoints + verify_api_key dep
│   ├── dashboard.py             # build_dashboard() : agrégation cards PWA
│   ├── profile.py               # UserProfile : chargement YAML data/profile.yaml
│   ├── config.py                # Settings dataclass + load_settings()
│   ├── logging_conf.py          # structlog setup
│   ├── sentry_setup.py          # opt-in Sentry init + capture_exception helper
│   ├── cache.py                 # TTLCache (LRU async) — LLM opt-in + SearXNG
│   ├── db.py                    # AsyncEngine partagé + WAL mode
│   ├── http_retry.py            # httpx retry + JSON helper (Open-Meteo, ODS, …)
│   │
│   ├── pipeline/                # cœur transport-agnostic (package)
│   │   ├── __init__.py          # API publique : BotDeps, process_message(+_stream),
│   │   │                        #   StreamEvent, MAX_HISTORY, FALLBACK_TEXT
│   │   ├── core.py              # BotDeps + StreamEvent + orchestrateurs + helpers partagés
│   │   ├── dates.py             # parsing de dates FR (parse_due, parse_range, …)
│   │   ├── side_effects.py      # apply_side_effects (memory/task/depot/expense)
│   │   └── handlers.py          # run_intent_handler + handle_feed/event/fuel/weather
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
│   ├── chat/
│   │   ├── models.py            # ChatMessage (shares Base with tasks)
│   │   └── manager.py           # ChatHistoryManager (add_exchange / page / purge)
│   │
│   ├── calendar/
│   │   ├── models.py            # CalendarEvent dataclass
│   │   └── client.py            # ICloudCalendarClient (connect + fuzzy match)
│   │
│   ├── weather/
│   │   └── client.py            # OpenMeteoClient + HourlyPrecipitation + FR codes
│   │
│   ├── fuel/
│   │   ├── models.py            # FuelType + FuelStation (+ brand) + GeoPoint + FR synonyms
│   │   ├── client.py            # FuelClient (data.economie.gouv.fr ODS v2.1)
│   │   ├── geocoding.py         # NominatimClient (OSM FR + in-memory cache)
│   │   └── overpass.py          # OverpassClient (enseigne OSM amenity=fuel, enrichissement fail-soft)
│   │
│   ├── finance/
│   │   ├── models.py            # Expense + BudgetCycle (shares Base with tasks)
│   │   ├── manager.py           # ExpenseManager (add_* + tick_recurring_once + cycles)
│   │   ├── budget.py            # compute_budget + PendingRecurring (pure)
│   │   ├── config.py            # FinanceConfig : récurrentes lues du YAML profil
│   │   ├── csv_export.py        # build_expenses_csv (locale FR)
│   │   └── cron.py              # FinanceReminderJob (rappel quotidien récurrentes)
│   │
│   ├── thoughts/
│   │   ├── models.py            # Thought (intent depot — shares Base with tasks)
│   │   ├── manager.py           # ThoughtManager (create / list_* / close / mark_surfaced)
│   │   ├── restitution.py       # heuristiques pures (select_candidates, is_loop)
│   │   └── foryou.py            # ForYouBuilder.build (card "Pour toi", fail-soft + LLM)
│   │
│   ├── locations/
│   │   ├── models.py            # LocationEvent (shares Base with tasks)
│   │   ├── store.py             # LocationEventStore (record_event + current location)
│   │   └── presence.py          # dérivation de la position courante
│   │
│   ├── news/
│   │   └── client.py            # NewsCurator (SearXNG news + curation LLM)
│   │
│   └── proactivity/
│       ├── models.py            # NotificationLog (shares Base with tasks)
│       ├── rules.py             # evaluate_rain + evaluate_upcoming_event (pure)
│       └── service.py           # ProactivityService.tick + on_location_event + safeguards
│
├── frontend/                    # PWA React 18 + TS + Vite + Tailwind 3 (miroir domestique-ai)
│   ├── package.json             # deps front épinglées + scripts (dev/build)
│   ├── vite.config.ts           # build + proxy dev vers :8000 (map API_PREFIXES)
│   ├── tailwind.config.js       # tokens couleur → rgb(var(--x) / <alpha>) ; typography
│   ├── tsconfig.json            # TS strict (+ noUnusedLocals/Parameters)
│   ├── index.html               # shell HTML (head PWA, <link> icônes racine)
│   ├── public/                  # copié verbatim dans dist (assets non hashés)
│   │   ├── manifest.json
│   │   ├── sw.js                # service worker network-first, CACHE_NAME versionné
│   │   ├── favicon.svg
│   │   └── icon-{192,512,1024}.png
│   ├── dist/                    # build Vite (gitignoré) — servi par SPAStaticFiles
│   └── src/
│       ├── main.tsx             # entrée : bootstrapConfig + providers + render
│       ├── App.tsx              # écran unique (dashboard + overlays via état local)
│       ├── index.css            # palette (variables CSS dark/light) + CSS portée verbatim
│       ├── api/                 # client.ts (fetch same-origin + SSE) + types.ts (miroir Pydantic)
│       ├── components/          # dashboard/ (cards) + overlays/ + chat/ + Composer/Markdown/Toast
│       ├── hooks/               # useDashboard/useHistory/useChatStream/useNews/useSpeechRecognition
│       └── lib/                 # format, weatherIcon, chatStore, foryouCache (état hors React)
│
├── data/                        # persisted Docker volume
│   ├── chroma/
│   ├── profile.yaml             # profil utilisateur YAML (édité à la main)
│   ├── tasks.db                 # SQLite : tasks + feeds + notification_logs +
│   │                            #   pending_notifications + thoughts + expenses +
│   │                            #   budget_cycles + location_events + chat_messages
│   └── scheduler.db             # persisted APScheduler jobs
│
└── tests/                       # pytest-asyncio, everything mocked (no external I/O)
    ├── conftest.py
    ├── test_api.py / test_dashboard.py
    ├── test_pipeline_process.py / test_pipeline_stream.py / test_pipeline_dates.py
    ├── test_parser.py / test_llm_client.py / test_memory.py / test_embedder.py
    ├── test_finance_{manager,budget,config,cron,csv}.py
    ├── test_tasks.py / test_thoughts.py / test_feeds.py / test_calendar.py
    ├── test_weather.py / test_fuel_client.py / test_nominatim.py / test_news_curator.py
    ├── test_proactivity_{models,rules,service}.py / test_scheduler_interval.py
    ├── test_notifications_store.py / test_pushover.py / test_location_store.py
    └── test_cache.py / test_config.py / test_http_retry.py / test_logging_conf.py / …
```
