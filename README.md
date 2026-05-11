# copain — personal HTTP assistant

<p align="center">
  <img src="copain_bot.png" alt="Logo copain" width="200">
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

Single-user personal assistant driven by natural French language, exposed as
a FastAPI HTTP service and called directly from an iOS Shortcut over
Tailscale. Partly self-hosted on a Raspberry Pi 5 (local services) + cloud LLM.

## Features

- Conversation with automatic semantic memory (ChromaDB HNSW + batch embeddings)
- Tasks + push reminders in natural language (consumed via `GET /notifications`)
- Web search (self-hosted SearXNG, summarised in French, TTL cache)
- RSS feeds (add/list/summarise latest news on demand)
- Automatic morning briefing at 8am: weather + tasks + events + top 5 RSS,
  pushed into the `pending_notifications` queue
- Photo analysis via `POST /ask/image` (base64 payload)
- iCloud calendar via CalDAV (create + list events in any iCloud calendar,
  fuzzy name matching)
- Fuel prices around `HOME_CITY` (data.economie.gouv.fr open data)
- Weather via Open-Meteo, up to 16 days, FR expressions (`demain`, `ce weekend`)
- **Opt-in proactivity** (`PROACTIVITY_ENABLED=true`): rain alerts within the
  hour + appointment reminder ~1 h before. Built-in safeguards (time window,
  per-type cooldown, daily budget capped at 3).
- **Resilience**: TTL response cache (LLM opt-in + SearXNG always-on), optional
  local LLM fallback (`OLLAMA_FALLBACK_MODEL`) when the cloud is unreachable.
- **Monitoring**: opt-in Sentry error tracking (`SENTRY_DSN`, empty = disabled).

Routing between these capabilities is driven by the LLM through a `<meta>`
JSON block it emits at the end of every reply. See [`CLAUDE.md`](./CLAUDE.md)
for architecture details.

## HTTP endpoints

All endpoints require the `X-API-Key` header (matched against `API_KEY` from
`.env`). Missing or invalid → **403**.

| Method | Path             | Body                                                            |
| ------ | ---------------- | --------------------------------------------------------------- |
| POST   | `/ask`           | `{ "message": str }`                                            |
| POST   | `/ask/image`     | `{ "message": str, "image_b64": str, "media_type": str }`       |
| GET    | `/notifications` | —                                                               |

```bash
curl -H "X-API-Key: changeme" \
     -H "Content-Type: application/json" \
     -d '{"message":"bonjour"}' \
     http://localhost:8000/ask
```

## Stack

Python 3.12 async · FastAPI + uvicorn · Ollama (`gemma4:31b-cloud` for
the multimodal LLM, optional local `gemma3:4b` fallback, `nomic-embed-text`
for embeddings) · ChromaDB (HNSW) · SQLAlchemy 2 + aiosqlite · APScheduler ·
feedparser · caldav + vobject · httpx · structlog · Sentry SDK (opt-in).

## Local setup (dev)

```bash
cp .env.example .env          # then fill in the variables (see below)
make install                  # creates .venv, installs deps, installs pre-commit
make test                     # 213 tests, fully mocked (no external services)
make lint typecheck           # ruff + mypy strict
make run                      # starts uvicorn on API_PORT (requires real Ollama + SearXNG)
```

### Variables to fill in `.env`

See [`.env.example`](./.env.example) for the full list. The essentials:

- `API_KEY` — shared secret for `X-API-Key` (generate something random)
- `API_PORT` — uvicorn listen port (default 8000)
- `ICLOUD_USERNAME` — your Apple ID (login email)
- `ICLOUD_APP_PASSWORD` — **App-Specific Password** to generate (see below)
- `ICLOUD_CALENDAR_NAME` — default iCloud calendar name (fuzzy matching: you
  can write `Personnel` even if the real name contains emojis and surrounding
  spaces)

The other variables (`TZ`, `BRIEFING_*`, `HOME_*`, `OLLAMA_*`, etc.) have
reasonable defaults and can stay as-is for usage in Sélestat.

### Create an iCloud App-Specific Password

Required because of Apple ID 2FA:

1. Go to [appleid.apple.com](https://appleid.apple.com)
2. Sign-In and Security → **App-Specific Passwords** → Generate
3. Name the app (e.g. "copain bot")
4. Copy the password in the `xxxx-xxxx-xxxx-xxxx` format into `.env`

## Docker deployment (Pi 5)

```bash
make docker-build
make docker-up
docker logs -f copain-bot-1
```

Ollama must run **outside Docker** on the Pi (for GPU/NPU ARM access) with
`gemma4:31b-cloud` configured. The container uses `network_mode: host` so
`API_PORT` is exposed directly on the Pi and the bot can reach Ollama on
`localhost:11434`.

At startup, the logs should show:

- `startup env=... port=8000`
- `calendars_discovered count=N names=[...]`
- `calendar_connected calendar=...`
- `cron_job_scheduled job_id=daily-briefing hour=8`
- `api_lifespan_startup port=8000`

## Security

The API answers only callers presenting a valid `X-API-Key` header matching
`API_KEY`. Anything else returns 403 and the attempt is logged with the
source IP. The single-user model is enforced at the network layer
(Tailscale-only access) and at the auth layer (single shared secret).

## Documentation

- [`CLAUDE.md`](./CLAUDE.md) — detailed architecture, code conventions,
  system prompt, full project structure
- [`.env.example`](./.env.example) — environment variable template
