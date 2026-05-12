# copain — Personal HTTP Assistant — CLAUDE.md

## Project overview

Single-user personal assistant, entirely in natural French language. Exposed
as an HTTP API (FastAPI) called directly from an iOS Shortcut through a
Tailscale tunnel. Partly self-hosted (local services on a Raspberry Pi 5
8 GB, main LLM in the cloud).

### Current features

- **Conversation** with automatic semantic memory (ChromaDB + embeddings)
- **Tasks + reminders** in natural language (SQLite). Reminders are written
  to a `pending_notifications` table at due time; the iOS client polls
  `GET /notifications` to consume them.
- **Web search** via self-hosted SearXNG with FR summary
- **RSS feeds**: add/list/remove + summary of the latest news on demand
- **Morning briefing** automatically every day (configurable time, default
  8am): local weather + today's tasks + today's events + top 5 summarised RSS
  items, also enqueued into `pending_notifications`
- **Photo analysis**: image sent in base64 via `POST /ask/image` → LLM
  multimodal vision → routed through the normal pipeline
  (memory/task/event depending on content)
- **iCloud calendar** (CalDAV): event creation and listing in any iCloud
  calendar
- **Fuel prices**: via `data.economie.gouv.fr` open data API, top 5 stations
  around `HOME_CITY` (geocoding via OSM Nominatim)
- **Weather**: via Open-Meteo, supports FR expressions (`demain`, `ce
  weekend`, etc.) up to 16 days
- **Opt-in proactivity** (`PROACTIVITY_ENABLED=true`): rain alerts + event
  reminders with five safeguards (feature flag, time window, daily budget,
  dedup, cooldown). Disabled by default.
- **Dashboard PWA**: l'iPhone tape `/` et reçoit une PWA orientée "tableau
  de bord" (cards météo / prochain évent / tâches / notifs / briefing
  accordéon + raccourcis carburant et RSS). `GET /dashboard` agrège l'état
  en un seul appel. Mode chat optionnel via icône 💬 pour les conversations
  longues.
- **Profil utilisateur YAML** (`data/profile.yaml`): fichier édité à la main
  décrivant l'utilisateur (identité, famille, travail, voiture, routines,
  préférences). Injecté tel quel dans le system prompt à chaque appel LLM,
  avant le contexte mémoire RAG.

Everything flows through the same pipeline: an LLM decides the intent via a
`<meta>` JSON block, the code executes the side effects, then a text reply
is returned in the HTTP response. Proactive notifications, on the other
hand, run through an autonomous job (no LLM, no `<meta>` routing) that
writes into the `notification_logs` table to track cooldowns and budget
AND into the `pending_notifications` queue for the iOS client to consume.

---

## Architecture

```text
iOS Shortcut (over Tailscale)
        │
        ▼ HTTPS — X-API-Key
FastAPI app (bot/api.py, served by uvicorn)
        │
        ├── verify_api_key dep (X-API-Key vs settings.api_key, 403 if invalid)
        │
        ├── Endpoints
        │     ├── GET  /             → FileResponse(index.html) → Safari iOS (PWA dashboard)
        │     ├── GET  /config       → { api_key }  (pas d'auth, réseau Tailscale privé)
        │     ├── POST /ask           → pipeline.process_message(message) → { response, intent, refresh_cards }
        │     ├── POST /ask/image     → idem avec image (multimodal) → { response, intent, refresh_cards }
        │     ├── GET  /notifications → NotificationStore.get_unread() + mark_read()
        │     └── GET  /dashboard     → build_dashboard(): météo + next évent + tâches du jour + count notifs + briefing
        │
        ├── Pipeline (bot/pipeline.py, transport-agnostic)
        │     └── process_message(text, images?) → str
        │
        ├── LLM Client (Ollama — gemma4:31b-cloud multimodal + optional local fallback)
        │     ├── call(system, user, images?)        → Ollama chat API
        │     ├── call_with_search(message, results) → re-run with SearXNG results
        │     ├── chat(messages, cacheable=False)    → low-level call (opt-in cache)
        │     ├── chat_stream(messages)              → streaming chunks (currently unused by the API)
        │     └── TTLCache (bot.cache)               → LLM opt-in + SearXNG always-on
        │
        ├── Observability (optional)
        │     ├── bot.sentry_setup.configure_sentry  → opt-in via SENTRY_DSN (empty = no-op)
        │     └── capture_exception(exc, **context)  → API + APScheduler listeners
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
        │           ├── SQLAlchemyJobStore → persisted one-shot reminders (write into NotificationStore)
        │           └── MemoryJobStore     → cron (non-serialisable closures)
        │
        ├── NotificationStore (bot/notifications/store.py)
        │     ├── add(text, title, priority, sound) → SQLite row + Pushover push
        │     ├── get_unread()                      → read FIFO
        │     └── mark_read(ids)                    → stamp read_at
        │
        ├── PushoverClient (bot/notifications/pushover.py)
        │     └── push(message, title, priority, sound) → api.pushover.net → iOS
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
              └── send_daily → enqueues the briefing into NotificationStore
```

---

## Tech stack

| Component     | Choice                                                  |
| ------------- | ------------------------------------------------------- |
| Language      | Python 3.12+ async/await everywhere                     |
| HTTP API      | FastAPI + uvicorn[standard]                             |
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
| Tests         | pytest + pytest-asyncio (auto mode, 215 tests)          |
| Quality       | ruff (lint+format) + mypy strict via pre-commit         |
| Interface web | Vanilla JS PWA, servie par FastAPI à `/`                |
| Monitoring    | Sentry SDK (opt-in via `SENTRY_DSN`)                    |
| Push iOS      | Pushover (opt-in via `PUSHOVER_TOKEN` + `PUSHOVER_USER`)|

---

## HTTP endpoints

All endpoints require the `X-API-Key` header matching `settings.api_key`.
Missing or invalid → 403 with a warning logged (source IP included).

| Method | Path             | Body                                                            | Response                                                                                                |
| ------ | ---------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| POST   | `/ask`           | `{ "message": str }`                                            | `{ "response": str, "intent": str, "refresh_cards": [str] }`                                            |
| POST   | `/ask/image`     | `{ "message": str, "image_b64": str, "media_type": str }`       | `{ "response": str, "intent": str, "refresh_cards": [str] }`                                            |
| GET    | `/notifications` | —                                                               | `{ "notifications": [ { "id": int, "text": str, "created_at": str } ] }`                                |
| GET    | `/dashboard`     | —                                                               | `{ "weather": …, "next_event": …, "today_tasks": […], "unread_notifications": int, "briefing": … }`     |

Quick smoke test:

```bash
curl -H "X-API-Key: changeme" \
     -H "Content-Type: application/json" \
     -d '{"message":"bonjour"}' \
     http://localhost:8000/ask
```

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

**The API only answers callers holding the right `X-API-Key`.** Enforced
via a FastAPI dependency on every endpoint:

```python
async def verify_api_key(
    request: Request,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings_dep),
) -> None:
    if x_api_key != settings.api_key:
        log.warning("api_access_denied", ip=request.client.host)
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
```

The expected key lives in `API_KEY` (env). Generate something random and
keep it out of git. The single-user model is enforced at the network layer
(Tailscale-only access) and at the auth layer (single shared secret).

---

## Path-scoped rules

Detailed guidance lives in `.claude/rules/` and is loaded only when Claude
reads files matching the rule's `paths` pattern:

| Rule file               | Loaded on                                                         |
| ----------------------- | ----------------------------------------------------------------- |
| `python-conventions.md` | `bot/**/*.py`, `tests/**/*.py`                                    |
| `project-structure.md`  | `bot/**/*.py`, `tests/**/*.py`                                    |
| `config-env.md`         | `bot/config.py`, `.env*`, `docker-compose.yml`, `Dockerfile`      |
| `api.md`                | `bot/api.py`, `bot/pipeline.py`, `bot/main.py`                    |
| `llm.md`                | `bot/llm/**`                                                      |
| `calendar.md`           | `bot/calendar/**`                                                 |
| `scheduler.md`          | `bot/tasks/scheduler.py`, `bot/briefing/**`, `bot/proactivity/**` |
