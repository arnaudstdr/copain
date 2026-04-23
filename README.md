# copain — personal Telegram assistant

<p align="center">
  <img src="copain_bot.png" alt="Logo copain" width="200">
</p>

<p align="center">
  <a href="https://github.com/arnaudstdr/copain/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/arnaudstdr/copain/ci.yml?branch=main&label=CI&logo=githubactions&logoColor=white" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/lint-ruff-000000?logo=ruff&logoColor=white" alt="Ruff">
  <img src="https://img.shields.io/badge/types-mypy%20strict-1f5082?logo=python&logoColor=white" alt="Mypy strict">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/platform-Telegram-26A5E4?logo=telegram&logoColor=white" alt="Telegram">
  <img src="https://img.shields.io/badge/LLM-Ollama-000000?logo=ollama&logoColor=white" alt="Ollama">
  <img src="https://img.shields.io/badge/deploy-Docker-2496ED?logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/host-Raspberry%20Pi%205-c51a4a?logo=raspberrypi&logoColor=white" alt="Raspberry Pi 5">
</p>

Single-user Telegram bot driven by natural French language.
Partly self-hosted on a Raspberry Pi 5 (local services) + cloud LLM.

## Features

- Conversation with automatic semantic memory
- Tasks + Telegram reminders in natural language
- Web search (self-hosted SearXNG, summarised in French)
- RSS feeds (add/list/summarise latest news on demand)
- Automatic morning briefing at 8am: weather + tasks + events + top 5 RSS
- Photo analysis (text, scene, chart, menu, receipt, etc.)
- iCloud calendar via CalDAV (create + list events in any iCloud calendar,
  fuzzy name matching)
- **Opt-in proactivity** (`PROACTIVITY_ENABLED=true`): rain alerts within the
  hour + appointment reminder ~1 h before. Built-in safeguards (time window,
  per-type cooldown, daily budget capped at 3).

Routing between these capabilities is driven by the LLM through a `<meta>`
JSON block it emits at the end of every reply. See [`CLAUDE.md`](./CLAUDE.md)
for architecture details.

## Stack

Python 3.12 async · python-telegram-bot v21 · Ollama (`gemma4:31b-cloud` for
the multimodal LLM, `nomic-embed-text` local for embeddings) · ChromaDB ·
SQLAlchemy 2 + aiosqlite · APScheduler · feedparser · caldav + vobject ·
httpx · structlog.

## Local setup (dev)

```bash
cp .env.example .env          # then fill in the variables (see below)
make install                  # creates .venv, installs deps, installs pre-commit
make test                     # 59 tests, fully mocked (no external services)
make lint typecheck           # ruff + mypy strict
make run                      # starts the bot (requires real Ollama + SearXNG)
```

### Variables to fill in `.env`

See [`.env.example`](./.env.example) for the full list. The essentials:

- `TELEGRAM_BOT_TOKEN` — bot token (via @BotFather)
- `ALLOWED_USER_ID` — your Telegram user ID (get it via @userinfobot)
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
`gemma4:31b-cloud` configured.

At startup, the logs should show:

- `startup env=...`
- `calendars_discovered count=N names=[...]`
- `calendar_connected calendar=...`
- `cron_job_scheduled job_id=daily-briefing hour=8`

## Security

The bot silently ignores any message from a user whose ID does not match
`ALLOWED_USER_ID`. Unauthorised access attempts are logged as warnings.

## Documentation

- [`CLAUDE.md`](./CLAUDE.md) — detailed architecture, code conventions,
  system prompt, full project structure
- [`.env.example`](./.env.example) — environment variable template
