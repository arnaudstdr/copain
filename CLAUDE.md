# copain — Personal HTTP Assistant — CLAUDE.md

## Project overview

Single-user personal assistant, entirely in natural French language. Exposed
as an HTTP API (FastAPI) called directly from an iOS Shortcut through a
Tailscale tunnel. Partly self-hosted (local services on a Raspberry Pi 5
8 GB, main LLM in the cloud).

### Product positioning

copain n'est pas un assistant productiviste — c'est un **cerveau d'appoint**
pensé pour quelqu'un avec TDA/H + anxiété : il doit **absorber la charge
mentale**, pas en rajouter. Toute nouvelle feature passe le filtre « est-ce
que ça sort quelque chose de la tête de l'utilisateur, ou est-ce que ça en
rajoute ? ». Conséquence concrète : pas de pushs spontanés non sollicités
(le briefing matin a été retiré), priorité aux **dépôts** (`intent=depot`)
pour vider des pensées parasites sans tenter de les traiter.

### Current features

- **Conversation** with automatic semantic memory (ChromaDB + embeddings)
- **Tasks + reminders** in natural language (SQLite). Reminders are written
  to a `pending_notifications` table at due time; the iOS client polls
  `GET /notifications` to consume them.
- **Décharge cognitive (`intent=depot`)** : l'utilisateur dépose une pensée
  parasite ("j'ai peur pour X", "j'ai eu une idée Y", "note Z") et le LLM
  l'accuse sobrement (1-3 mots). Persistée dans la table `thoughts`
  (`id, content, kind, created_at, processed_at, surfaced_at`) + indexée
  dans ChromaDB avec tag `{kind: "depot", thought_kind, thought_id}`.
  `kind` ∈ `worry | idea | note`. Consultable via `GET /thoughts?since=&limit=`.
  Au dépôt, détection de **boucle de rumination** (≥ N dépôts similaires sur
  30 j via embeddings) → suffixe sobre dans l'accusé. La **clôture en langage
  naturel** d'un souci ouvert est possible (`depot.action=close`, le LLM ne
  peut clore que ce qu'il a vu dans la section « Soucis ouverts » du prompt).
- **Restitution des dépôts (card « Pour toi », `GET /foryou`)** : canal
  100 % pull (fetch au tap, jamais poussé) qui ressort sobrement les dépôts
  méritant un regard — souci rapprochable d'un évent passé (« closable »),
  boucle de rumination, idée ancienne. L'orchestrateur (`ForYouBuilder`,
  `bot/thoughts/foryou.py`) collecte en fail-soft, applique les heuristiques
  pures de `bot/thoughts/restitution.py` (priorités, fenêtres, cooldown via
  `surfaced_at`) puis fait formuler chaque item par le LLM. La card du
  dashboard reste **neutre** (pas de compteur entrant) ; l'overlay porte une
  action par item (« C'est réglé » → `POST /thoughts/{id}/close` sur chaque
  dépôt membre · « Garder » → masquage local). État apaisant si rien à sortir.
- **Web search** via self-hosted SearXNG with FR summary
- **RSS feeds**: add/list/remove + summary of the latest news on demand
- **Card Actu (curation IA, fetch au tap)** : `GET /news/latest` interroge
  SearXNG (news 24h) + LLM (curation/résumé) selon les topics du profil
  YAML (`news_topics.daily_briefing`). La card du dashboard affiche les
  états idle/loading/data ; le tap ouvre l'overlay markdown.
- **Photo analysis**: image sent in base64 via `POST /ask/image` → LLM
  multimodal vision → routed through the normal pipeline
  (memory/task/event depending on content)
- **iCloud calendar** (CalDAV): event creation and listing in any iCloud
  calendar
- **Fuel prices** (intent `fuel` LLM uniquement, plus de card dashboard) :
  via `data.economie.gouv.fr` open data API, top 5 stations autour de
  `HOME_CITY` (geocoding via OSM Nominatim)
- **Weather**: via Open-Meteo, supports FR expressions (`demain`, `ce
  weekend`, etc.) up to 16 days
- **Budget / finances (`intent=expense`)** : saisie en langage naturel des
  dépenses ponctuelles (`spend`), revenus (`income`, peut ancrer un nouveau
  cycle budgétaire via `starts_cycle`) et pointages de récurrentes
  (`tick_recurring`, loyer/PEL définis dans le YAML `finances.recurring`).
  Le cycle budgétaire est ancré sur la date de perception du salaire (table
  `budget_cycles`), fallback mois civil. Card Budget sur le dashboard
  (`compute_budget` : restant prévisionnel = revenus − dépenses −
  récurrentes non pointées), overlay détail via `GET /budget`, export
  tableur via `GET /expenses/export.csv`, rappel quotidien des récurrentes
  dues non pointées via `FinanceReminderJob` (cron APScheduler,
  `bot/finance/cron.py`).
- **Opt-in proactivity** (`PROACTIVITY_ENABLED=true`): rain alerts + event
  reminders with five safeguards (feature flag, time window, daily budget,
  dedup, cooldown). Disabled by default.
- **Dashboard PWA**: l'iPhone tape `/` et reçoit une PWA orientée "tableau
  de bord" (cards météo / prochain évent / tâches / notifs / actu).
  `GET /dashboard` agrège l'état en un seul appel. Mode chat optionnel via
  icône 💬 pour les conversations longues. **Plus de briefing matin
  automatique ni de card carburant** (intentionnellement, pour ne pas
  pousser d'info entrante non sollicitée). Code sous `bot/static/` :
  `index.html` (structure seule, servi en `no-store`), CSS sous `styles/`,
  JS en modules ES6 natifs sous `js/` (entrée `main.js`, assets référencés
  avec `?v=N` incrémenté à chaque déploiement pour invalider le cache
  Safari).
- **Profil utilisateur YAML** (`data/profile.yaml`): fichier édité à la main
  décrivant l'utilisateur (identité, famille, travail, voiture, routines,
  préférences). Injecté tel quel dans le system prompt à chaque appel LLM,
  avant le contexte mémoire RAG.
- **Voix Siri**: raccourci iOS "Dis à Copain" qui POST sur `/ask` avec un
  header `X-Source: siri`. Le bot adapte alors son system prompt pour
  produire des réponses TTS-friendly (1-2 phrases, pas de markdown ni
  d'emoji). Voir `docs/ios-shortcuts.md`.
- **Localisation iPhone**: les automations iOS POSTent sur
  `POST /event/location` à chaque arrivée/départ d'une géofence (maison,
  bureau, …). Les events sont persistés dans `location_events` et la
  position courante est dérivée (logique "dernier event gagne"). Elle est
  injectée dans le system prompt pour que le LLM sache où se trouve
  l'utilisateur. La card météo du dashboard est aussi contextualisée
  (bureau → Obernai, sinon → Sélestat).
- **Proactivité event-driven**: en plus du tick cron (pluie + RDV -1h),
  l'endpoint `POST /event/location` déclenche
  `ProactivityService.on_location_event` qui peut pousser un "briefing
  retour" au départ du bureau le soir (cooldown 4h, mêmes garde-fous que
  le tick cron). À la création d'évent calendrier, détection de
  chevauchement et warning textuel dans la réponse (l'évent est créé
  quand même).

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
        │     │                          (header X-Source: siri active le voice_mode TTS)
        │     ├── POST /ask/stream    → pipeline.process_message_stream(message) → SSE (mode dialogue PWA)
        │     │                          frames data:{type: delta|replace|done|error, …}
        │     ├── POST /ask/image     → idem avec image (multimodal) → { response, intent, refresh_cards }
        │     ├── GET  /notifications → NotificationStore.get_unread() + mark_read()
        │     ├── GET  /dashboard     → build_dashboard(): météo + next évent + tâches du jour + count notifs + budget
        │     ├── GET  /news/latest   → NewsCurator.fetch_top_news() → { markdown, fetched_at } (card Actu)
        │     ├── GET  /thoughts      → ThoughtManager.list_recent/list_since → liste des dépôts cognitifs
        │     ├── POST /thoughts/{id}/close → ThoughtManager.close(id) → tap "C'est réglé" (404 si inconnu)
        │     ├── GET  /foryou        → ForYouBuilder.build() → card "Pour toi" (restitution, fail-soft)
        │     ├── GET  /tasks         → TaskManager.list_pending() → overlay tâches PWA (cochage)
        │     ├── POST /tasks/{id}/complete → TaskManager.complete(id)
        │     ├── GET  /budget        → compute_budget() détaillé (transactions + pending) → overlay Budget
        │     ├── GET  /expenses/export.csv → build_expenses_csv(from, to) → CSV locale FR
        │     ├── GET  /weather/forecast → Open-Meteo brut (horaire 24h + 7 jours), lieu selon position courante
        │     ├── GET  /events        → ICloudCalendarClient.list_all_upcoming(days) → overlay agenda
        │     └── POST /event/location → LocationEventStore.record_event() → { recorded, current_place }
        │
        ├── Pipeline (package bot/pipeline/, transport-agnostic)
        │     ├── core.py          → BotDeps + StreamEvent + orchestrateurs + helpers partagés
        │     │     ├── process_message(text, images?) → (str, Meta)
        │     │     └── process_message_stream(text)   → AsyncIterator[StreamEvent] (delta/replace/done)
        │     ├── dates.py         → parsing de dates FR (parse_due, parse_range, …)
        │     ├── side_effects.py  → apply_side_effects (memory/task/depot/expense)
        │     └── handlers.py      → run_intent_handler + handle_feed/event/fuel/weather
        │
        ├── LLM Client (Ollama — gemma4:31b-cloud multimodal + optional local fallback)
        │     ├── call(system, user, images?)        → Ollama chat API
        │     ├── call_with_search(message, results) → re-run with SearXNG results
        │     ├── call_with_search_stream(…)         → idem, streamé (intent search via /ask/stream)
        │     ├── chat(messages, cacheable=False)    → low-level call (opt-in cache)
        │     ├── chat_stream(messages)              → streaming chunks (utilisé par /ask/stream)
        │     └── TTLCache (bot.cache)               → LLM opt-in + SearXNG always-on
        │
        ├── Observability (optional)
        │     ├── bot.sentry_setup.configure_sentry  → opt-in via SENTRY_DSN (empty = no-op)
        │     └── capture_exception(exc, **context)  → API + APScheduler listeners
        │
        ├── <meta> parser
        │     └── Intent ∈ {answer, task, search, memory, feed, event, fuel, weather, depot, expense}
        │         + TaskMeta / FeedMeta / EventMeta / FuelMeta / WeatherMeta / DepotMeta / ExpenseMeta
        │
        ├── Memory Manager (ChromaDB + nomic-embed-text via Ollama)
        │     ├── store()             → embed + persist the memory_content
        │     ├── store_depot()       → embed + tag {kind=depot, thought_id, thought_kind}
        │     └── retrieve_context()  → top-5 relevant chunks
        │
        ├── Task Manager (SQLite via SQLAlchemy async + aiosqlite)
        │     ├── create / list_pending / complete / delete
        │     └── ReminderScheduler
        │           ├── SQLAlchemyJobStore → persisted one-shot reminders (write into NotificationStore)
        │           └── MemoryJobStore     → cron (non-serialisable closures)
        │
        ├── Thought Manager (SQLite — table `thoughts`)
        │     └── create / list_recent / list_since / list_open / close /
        │         mark_surfaced (intent `depot` + restitution)
        │
        ├── Restitution des dépôts (card "Pour toi" — bot/thoughts/)
        │     ├── restitution.py → heuristiques pures (select_candidates, is_loop)
        │     └── foryou.py      → ForYouBuilder.build (collecte fail-soft +
        │                          rapprochement worry↔évent lexical + LLM)
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
        ├── Fuel (open data fuel prices — intent LLM uniquement)
        │     ├── FuelClient         → data.economie.gouv.fr (ODS v2.1)
        │     └── NominatimClient    → OSM geocoding (FR, in-memory cache)
        │
        ├── Finance (intent `expense` — bot/finance/)
        │     ├── ExpenseManager     → tables `expenses` + `budget_cycles` (cycle ancré salaire)
        │     │     ├── add_punctual / add_income / start_cycle
        │     │     └── tick_recurring_once (atomique : check + insert sous lock)
        │     ├── compute_budget     → restant prévisionnel (bot/finance/budget.py, pur)
        │     ├── FinanceConfig      → récurrentes lues du YAML profil (finances.recurring)
        │     ├── build_expenses_csv → export CSV locale FR (GET /expenses/export.csv)
        │     └── FinanceReminderJob → cron quotidien : question Pushover si récurrente due non pointée
        │
        └── News Curator (card Actu, fetch au tap)
              ├── SearxngClient (categories=news, time_range=day)
              └── LLM (curation + résumé 1-2 lignes par article)
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
| Tests         | pytest + pytest-asyncio (auto mode, 460+ tests)         |
| Quality       | ruff (lint+format) + mypy strict via pre-commit         |
| Interface web | Vanilla JS PWA (modules ES6 natifs, zéro build step)    |
| Monitoring    | Sentry SDK (opt-in via `SENTRY_DSN`)                    |
| Push iOS      | Pushover (opt-in via `PUSHOVER_TOKEN` + `PUSHOVER_USER`)|

---

## HTTP endpoints

All endpoints require the `X-API-Key` header matching `settings.api_key`.
Missing or invalid → 403 with a warning logged (source IP included).

| Method | Path               | Body                                                              | Response                                                                                                |
| ------ | ------------------ | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| POST   | `/ask`             | `{ "message": str }` <br>(header `X-Source: siri` → mode vocal)   | `{ "response": str, "intent": str, "refresh_cards": [str] }`                                            |
| POST   | `/ask/stream`      | `{ "message": str }`                                              | SSE `text/event-stream` — frames `data: { "type": "delta"\|"replace"\|"done"\|"error", … }`             |
| POST   | `/ask/image`       | `{ "message": str, "image_b64": str, "media_type": str }`         | `{ "response": str, "intent": str, "refresh_cards": [str] }`                                            |
| GET    | `/notifications`   | —                                                                 | `{ "notifications": [ { "id": int, "text": str, "created_at": str } ] }`                                |
| GET    | `/dashboard`       | —                                                                 | `{ "weather": …, "next_event": …, "today_tasks": […], "unread_notifications": int }`                    |
| GET    | `/news/latest`     | —                                                                 | `{ "markdown": str, "fetched_at": str }`                                                                |
| GET    | `/thoughts`        | `?since=<ISO>&limit=<int>` (optionnels)                           | `{ "thoughts": [ { "id": int, "content": str, "kind": str\|null, "created_at": str } ] }`              |
| POST   | `/thoughts/{id}/close` | —                                                             | `{ "closed": bool, "thought_id": int }` (idempotent, 404 si id inconnu)                                 |
| GET    | `/foryou`          | —                                                                 | `{ "items": [ { "type": str, "message": str, "thought_ids": [int] } ], "fetched_at": str }`             |
| GET    | `/tasks`           | —                                                                 | `{ "tasks": [ { "id": int, "content": str, "due_at": str\|null } ] }` (tâches en cours)                |
| POST   | `/tasks/{task_id}/complete` | —                                                        | tâche marquée terminée (overlay PWA)                                                                    |
| GET    | `/budget`          | —                                                                 | détail du cycle courant : transactions + récurrentes pending (overlay Budget)                           |
| GET    | `/expenses/export.csv` | `?from=YYYY-MM-DD&to=YYYY-MM-DD` (bornes incluses)             | CSV FR (sep `;`, virgule décimale, UTF-8 BOM, dates `JJ/MM/AAAA`) en `attachment`                       |
| GET    | `/weather/forecast` | `?days=<int>&hours=<int>` (optionnels)                           | prévisions Open-Meteo brutes (horaire + quotidien), lieu selon position courante                        |
| GET    | `/events`          | `?days=<int>` (optionnel, défaut 7, max 60)                       | `{ "events": […] }` — évents iCloud à venir, tous calendriers                                           |
| POST   | `/event/location`  | `{ "event": "arrived"\|"left", "place": str, "lat"?, "lon"?, "at"? }` | `{ "recorded": bool, "current_place": str \| null }`                                                |

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
  "intent": "answer|task|search|memory|feed|event|fuel|weather|depot|expense",
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
  "depot": {
    "content": "raw thought verbatim if intent=depot and action=add, otherwise null",
    "kind": "worry|idea|note if intent=depot and action=add, otherwise null",
    "action": "add|close if intent=depot (default add; close = NL closure of a listed open worry), otherwise null",
    "thought_id": "id of the worry to close (taken from the 'Soucis ouverts' prompt section) if action=close, otherwise null"
  },
  "expense": {
    "action": "spend|income|tick_recurring if intent=expense, otherwise null",
    "amount": "amount in euros (number) if given, otherwise null (tick falls back to the YAML amount)",
    "label": "short label of the entry, otherwise null",
    "category": "free category if action=spend, otherwise null",
    "recurring_key": "YAML key (finances.recurring) if action=tick_recurring, otherwise null",
    "when": "FR expression ('hier', 'le 5') if mentioned, otherwise null (= today, past-biased)",
    "shared": "true if paid from the joint account (excluded from personal budget), default false",
    "starts_cycle": "true when action=income marks the salary reception (anchors a new budget cycle), default false"
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
| `api.md`                | `bot/api.py`, `bot/pipeline/**`, `bot/main.py`                    |
| `llm.md`                | `bot/llm/**`                                                      |
| `calendar.md`           | `bot/calendar/**`                                                 |
| `scheduler.md`          | `bot/tasks/scheduler.py`, `bot/finance/cron.py`, `bot/proactivity/**` |
