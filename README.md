# copain — personal HTTP assistant

<p align="center">
  <img src="bot/static/favicon.svg" alt="Logo copain" width="160">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/lint-ruff-000000?logo=ruff&logoColor=white" alt="Ruff">
  <img src="https://img.shields.io/badge/types-mypy%20strict-1f5082?logo=python&logoColor=white" alt="Mypy strict">
  <img src="https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white" alt="FastAPI">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/LLM-Ollama-000000?logo=ollama&logoColor=white" alt="Ollama">
  <img src="https://img.shields.io/badge/deploy-Docker-2496ED?logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/host-Raspberry%20Pi%205-c51a4a?logo=raspberrypi&logoColor=white" alt="Raspberry Pi 5">
</p>

Single-user personal assistant driven by natural French language. Three
entry points, all served by the same FastAPI core over Tailscale :

1. **PWA dashboard** — Safari iOS opens `/` and gets a dashboard web
   app with cards (weather, next event, tasks, notifications, news,
   budget, "pour toi") + interactive overlays (tasks, budget detail,
   thought restitution). Optional chat mode (💬) with SSE streaming.
2. **Siri voice shortcut** — "Dis à Copain…" sends the dictated text via
   `POST /ask` with `X-Source: siri`, gets back a TTS-friendly answer.
3. **Geofence automations** — iOS Shortcuts post arrival/departure events
   on `POST /event/location`, the bot keeps track of where you are and
   may push contextual notifications.

Hosted partly on a Raspberry Pi 5 (local services) with the main LLM in
the cloud (Ollama Cloud).

## Features

> **Product positioning** — copain is not a productivity assistant, it's a
> **backup brain**: it must **absorb mental load**, not pile more on. No
> unsolicited spontaneous pushes; priority goes to **deposits**
> (`intent=depot`) to get intrusive thoughts out of your head.

### Core conversation

- LLM routing via a `<meta>` JSON block emitted at the end of every reply
  (intent ∈ `answer | task | search | memory | feed | event | fuel | weather | depot | expense`).
- Automatic **semantic memory** (ChromaDB HNSW + batch embeddings).
- **User profile** (`data/profile.yaml`, hand-edited) injected as stable
  facts into the system prompt — the LLM knows your name, family, work,
  vehicles, routines, preferences.
- **Photo analysis** via `POST /ask/image` (multimodal LLM).
- **TTS-friendly mode** when the request carries `X-Source: siri` (1-2
  sentence answers, no markdown, no emoji).
- **SSE streaming** (`POST /ask/stream`) for the PWA chat mode : the reply
  streams token by token, the `<meta>` block is filtered on the fly.

### Cognitive offloading

- **Deposits (`intent=depot`)** — drop a parasitic thought ("j'ai peur pour
  X", "idée Y", "note Z"), the LLM acknowledges soberly (1-3 words).
  Persisted in `thoughts` (`worry | idea | note`) + indexed in ChromaDB.
  At deposit time, **rumination loop detection** (≥ N similar deposits over
  30 days) adds a sober suffix. Open worries can be **closed in natural
  language** (`depot.action=close`).
- **Restitution — card "Pour toi" (`GET /foryou`)** — 100 % pull channel
  (fetched on tap, never pushed) that gently surfaces deposits worth a
  look : a worry closable against a past event, a rumination loop, an old
  idea. The dashboard card stays neutral (no inbound counter) ; soothing
  state when there's nothing to surface.

### Productivity

- **Tasks + push reminders** in natural language. Reminders write to
  `pending_notifications` consumed by the PWA (or pushed via Pushover).
- **Interactive task overlay** in the PWA : tap on the "Tâches" card →
  see all pending tasks → tap to complete, swipe left to delete.
- **iCloud calendar** (CalDAV) : create + list events, fuzzy match on
  calendar name, overlap detection at creation time with a warning.
- **Budget / finances (`intent=expense`)** — natural-language entry of
  punctual spends (`spend`), incomes (`income`, can anchor a new budget
  cycle via `starts_cycle`) and recurring ticks (`tick_recurring`, rent /
  PEL from the YAML profile). Budget cycle anchored on the salary date
  (fallback civil month). Dashboard Budget card (forecast remaining),
  detail overlay (`GET /budget`), spreadsheet export
  (`GET /expenses/export.csv`), daily Pushover reminder for due unticked
  recurrings (`FinanceReminderJob`).
- **RSS feeds** : add / list / summarize latest news on demand.
- **News card "Actu" (`GET /news/latest`)** — AI curation fetched on tap :
  SearXNG (news 24h) + LLM summary per the profile's `news_topics`.
- **Web search** via self-hosted SearXNG, summarised in French.
- **Fuel prices** around `HOME_CITY` (`data.economie.gouv.fr` open data) —
  LLM intent only, no dashboard card.
- **Weather** via Open-Meteo (up to 16 days, FR expressions like
  `demain` / `ce weekend`). Dashboard card auto-switches to `WORK_*`
  coordinates when geofence says you're at work.

### Proactive notifications

Strictly opt-in via `PROACTIVITY_ENABLED=true`. Two channels :

- **Cron tick** (every 30 min) — rain alert in the next hour,
  appointment reminder ~1h before. Five safeguards : window, daily
  budget (3), per-kind cooldown, dedup by event UID.
- **Event-driven** (on `POST /event/location`) — a "return briefing" when
  you leave work after 5pm (cooldown 4h, same safeguards).

### iOS integration

- **iOS Shortcuts integration** : see [`docs/ios-shortcuts.md`](./docs/ios-shortcuts.md)
  for the Siri voice command and the four geofence automations.
- No automatic morning briefing (intentionally removed — no unsolicited
  inbound info ; the dashboard is pull-only).

### Resilience & monitoring

- **TTL response cache** (LLM opt-in + SearXNG always-on).
- **Optional local LLM fallback** (`OLLAMA_FALLBACK_MODEL`) when the
  cloud is unreachable. Fallback responses are never cached.
- **Sentry** opt-in error tracking (`SENTRY_DSN`, empty = disabled).
- **Pushover** opt-in iOS push notifications (`PUSHOVER_TOKEN/USER`).

## HTTP endpoints

All endpoints require the `X-API-Key` header (matched against `API_KEY`
from `.env`). Missing or invalid → **403**.

| Method | Path                       | Body / params                                                          |
| ------ | -------------------------- | ---------------------------------------------------------------------- |
| GET    | `/`                        | — (serves the PWA HTML)                                                |
| GET    | `/config`                  | — (returns `api_key` for the PWA, no auth required, Tailscale-only)    |
| POST   | `/ask`                     | `{ "message": str }` (header `X-Source: siri` → voice mode)            |
| POST   | `/ask/stream`              | `{ "message": str }` → SSE `text/event-stream` (PWA chat mode)         |
| POST   | `/ask/image`               | `{ "message": str, "image_b64": str, "media_type": str }`              |
| GET    | `/notifications`           | — (returns + marks as read)                                            |
| GET    | `/dashboard`               | — (weather + next event + today tasks + unread count + budget)         |
| GET    | `/news/latest`             | — (curated news card, fetched on tap)                                  |
| GET    | `/thoughts`                | `?since=<ISO>&limit=<int>` (optional) — cognitive deposits             |
| POST   | `/thoughts/{id}/close`     | — (close a worry, idempotent, 404 if unknown)                          |
| GET    | `/foryou`                  | — (card "pour toi", restitution, fail-soft)                            |
| GET    | `/tasks`                   | — (all pending tasks)                                                  |
| POST   | `/tasks/{id}/complete`     | — (mark task as done)                                                  |
| DELETE | `/tasks/{id}`              | — (delete task)                                                        |
| GET    | `/budget`                  | — (current cycle detail : transactions + pending recurrings)           |
| GET    | `/expenses/export.csv`     | `?from=YYYY-MM-DD&to=YYYY-MM-DD` → CSV FR (`;`, comma decimal, BOM)     |
| GET    | `/weather/forecast`        | `?days=<int>&hours=<int>` — raw Open-Meteo, location-aware             |
| GET    | `/events`                  | `?days=<int>` (default 7, max 60) — upcoming iCloud events             |
| POST   | `/event/location`          | `{ "event": "arrived"\|"left", "place": str, "lat"?, "lon"?, "at"? }`  |

Quick smoke test :

```bash
curl -H "X-API-Key: changeme" \
     -H "Content-Type: application/json" \
     -d '{"message":"bonjour"}' \
     http://localhost:8000/ask
```

## Stack

Python 3.12 async · FastAPI + uvicorn · Ollama (`gemma4:31b-cloud` for
the multimodal LLM, optional local `gemma3:4b` fallback,
`nomic-embed-text` for embeddings) · ChromaDB (HNSW) · SQLAlchemy 2 +
aiosqlite · APScheduler · feedparser · caldav + vobject · httpx ·
structlog · PyYAML · Sentry SDK (opt-in) · Pushover (opt-in) · vanilla
JS PWA served by FastAPI.

## Local setup (dev)

```bash
cp .env.example .env          # then fill in the variables (see below)
cp data/profile.example.yaml data/profile.yaml  # then edit with your info
make install                  # creates .venv, installs deps, pre-commit
make test                     # 660+ tests, fully mocked (no external services)
make lint typecheck           # ruff + mypy strict
make run                      # uvicorn on API_PORT (real Ollama + SearXNG required)
```

### Variables to fill in `.env`

See [`.env.example`](./.env.example) for the full list. The essentials :

- `API_KEY` — shared secret for `X-API-Key` (generate something random).
- `API_PORT` — uvicorn listen port (default 8000).
- `ICLOUD_USERNAME` — your Apple ID (login email).
- `ICLOUD_APP_PASSWORD` — **App-Specific Password** (see below).
- `ICLOUD_CALENDAR_NAME` — default iCloud calendar (fuzzy matching :
  `Personnel` matches `🧘 Personnel`).
- `HOME_LAT` / `HOME_LON` / `HOME_CITY` — used for weather + fuel
  defaults.
- `WORK_LAT` / `WORK_LON` / `WORK_CITY` — used for context-aware
  dashboard weather card.
- `PROFILE_PATH` — path to the user profile YAML (default
  `data/profile.yaml`).
- `PUSHOVER_TOKEN` / `PUSHOVER_USER` — optional, enables iOS push notifs.
- `SENTRY_DSN` — optional, enables error monitoring.

The other variables (`TZ`, `OLLAMA_*`, `PROACTIVITY_*`, `FINANCE_REMINDER_*`,
etc.) have reasonable defaults and can stay as-is for usage in Sélestat.

### Create an iCloud App-Specific Password

Required because of Apple ID 2FA :

1. Go to [appleid.apple.com](https://appleid.apple.com).
2. Sign-In and Security → **App-Specific Passwords** → Generate.
3. Name the app (e.g. "copain bot").
4. Copy the password in the `xxxx-xxxx-xxxx-xxxx` format into `.env`.

### User profile YAML

The file `data/profile.yaml` (gitignored) describes who you are : name,
city, family, work, vehicles, routines, preferences. It's injected into
the LLM system prompt at every call so the assistant has stable context
about you without having to re-discover it via RAG.

Copy `data/profile.example.yaml` as a starting point. Edit by hand
whenever your situation changes (rare). The bot needs a restart to pick
up changes (no live reload).

## iOS configuration

After the bot is running on the Pi, configure two Shortcuts on your
iPhone — see [`docs/ios-shortcuts.md`](./docs/ios-shortcuts.md) for the
step-by-step :

1. **"Dis à Copain"** — Siri voice shortcut for hands-free interaction.
2. **Geofence automations** — 4 silent automations (home arrived / left,
   work arrived / left) that POST to `/event/location`.

The PWA itself doesn't need any setup : open `https://<pi-tailscale-host>:8000/`
in Safari and "Add to Home Screen" — the manifest takes care of the rest
(splash screen, fullscreen mode, app icon).

## Docker deployment (Pi 5)

```bash
make docker-build
make docker-up
docker logs -f copain-bot-1
```

Ollama must run **outside Docker** on the Pi (for GPU/NPU ARM access)
with `gemma4:31b-cloud` configured. The container uses
`network_mode: host` so `API_PORT` is exposed directly on the Pi and the
bot reaches Ollama on `localhost:11434`.

At startup, the logs should show :

```
startup env=... port=8000
profile_loaded path=/app/data/profile.yaml top_keys=[...]
calendars_discovered count=N names=[...]
calendar_connected calendar=...
cron_job_scheduled job_id=finance-recurring-reminder hour=9
api_lifespan_startup port=8000
```

## Security

The API answers only callers presenting a valid `X-API-Key` header
matching `API_KEY`. Anything else returns 403 and the attempt is logged
with the source IP. The single-user model is enforced at the network
layer (Tailscale-only access) and at the auth layer (single shared
secret).

## Documentation

- [`CLAUDE.md`](./CLAUDE.md) — detailed architecture, code conventions,
  system prompt structure, full project tree.
- [`.env.example`](./.env.example) — environment variable template.
- [`docs/ios-shortcuts.md`](./docs/ios-shortcuts.md) — Apple Shortcuts
  setup (Siri voice command + geofence automations).
