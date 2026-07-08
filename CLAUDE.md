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
- **Recall de la mémoire (`intent=memory`)** : l'utilisateur interroge sa
  propre mémoire en langage naturel (« j'avais noté quoi sur le garage ? »).
  Miroir *lecture* de `store_memory` (écriture) : `memory_query` porte l'objet
  de la recherche, le pipeline appelle `retrieve_context` (toute la collection,
  souvenirs + dépôts) puis fait reformuler le résultat par le LLM
  (`call_with_recall` / `call_with_recall_stream`). Read-only (aucun side
  effect, aucune card rafraîchie) ; fail-soft → « Je n'ai rien noté là-dessus. »
  si la mémoire est vide ou l'embed indisponible.
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
  méritant un regard — souci rapprochable d'un évent passé ou **apaisé par un
  budget sain** (« closable »), boucle de rumination, **connexion** (deux
  dépôts sémantiquement proches sans former de boucle — versant *fertile* du
  même signal de proximité), idée ancienne. Priorité :
  `closable_worry > loop > connection > stale_idea` (la dédup par `thought_ids`
  règle seule la collision boucle/connexion). Le rapprochement souci↔évent
  combine un match **lexical** et un **booster sémantique** (embeddings via
  `MemoryManager.embed_texts`) fusionnés en union — fail-soft : Embedder KO →
  lexical seul (`FORYOU_EVENT_MAX_DISTANCE`). Un souci d'**argent** (détecté
  lexicalement, vocabulaire de base ∪ libellés des enveloppes/récurrentes) est
  proposé comme closable **uniquement si le budget est sain** (restant > 0,
  aucune récurrente en retard, aucune enveloppe dépassée — via
  `load_budget_summary`, `bot/finance/summary.py`) ; jamais quand il est tendu.
  Le champ `Candidate.context_kind` (`event` | `budget`) pilote la formulation
  sans multiplier les types côté PWA. L'orchestrateur (`ForYouBuilder`,
  `bot/thoughts/foryou.py`) collecte en fail-soft — une seule passe de
  similarité (`_gather_similar`, un embed par graine ouverte) alimente boucles
  ET connexions — applique les heuristiques pures de
  `bot/thoughts/restitution.py` (priorités, fenêtres, cooldown via
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
  `HOME_CITY` (geocoding via OSM Nominatim). La donnée officielle ne contient
  **aucune enseigne** : chaque station est enrichie a posteriori (fail-soft)
  par l'enseigne du point `amenity=fuel` OpenStreetMap le plus proche via
  Overpass (`bot/fuel/overpass.py`, appariement par distance).
- **Weather**: via Open-Meteo, supports FR expressions (`demain`, `ce
  weekend`, etc.) up to 16 days
- **Budget / finances (`intent=expense`)** : saisie en langage naturel des
  dépenses ponctuelles (`spend`), revenus (`income`, peut ancrer un nouveau
  cycle budgétaire via `starts_cycle`) et pointages de récurrentes
  (`tick_recurring`, loyer/PEL définis dans le YAML `finances.recurring`).
  Le cycle budgétaire est ancré sur la date de perception du salaire (table
  `budget_cycles`), fallback mois civil. Card Budget sur le dashboard
  (`compute_budget` : restant prévisionnel = revenus − dépenses −
  récurrentes non pointées) ; l'overlay Budget est un **panneau interactif**
  (`GET /budget` pour le récap + `POST /expenses` pour la saisie directe par
  formulaire, **sans LLM** : dépense / revenu / pointage de récurrente). Ce
  formulaire est un canal parallèle à l'`intent=expense` du bot — les deux
  réutilisent les mêmes méthodes `ExpenseManager`, donc aucune divergence de
  calcul possible. Export tableur via `GET /expenses/export.csv`, rappel
  quotidien des récurrentes dues non pointées via `FinanceReminderJob` (cron
  APScheduler, `bot/finance/cron.py`). Enfin, quand la finance est configurée,
  le **résumé budgétaire complet** (restant prévisionnel, revenu, dépensé,
  récurrentes en retard, enveloppes dépassées) est injecté dans le system
  prompt (`safe_budget_summary` → `_format_budget_section`) : le LLM peut
  répondre factuellement et sans dramatiser à une question sur l'état des
  finances (« comment vont mes finances ? », intent `answer`, aucun montant
  inventé), en un seul tour.
- **Opt-in proactivity** (`PROACTIVITY_ENABLED=true`): rain alerts + event
  reminders with five safeguards (feature flag, time window, daily budget,
  dedup, cooldown). Disabled by default.
- **Dashboard PWA**: l'iPhone tape `/` et reçoit une PWA orientée "tableau
  de bord" (cards météo / prochain évent / dépôt express + pour toi / budget /
  actu). `GET /dashboard` agrège l'état en un seul appel. La **card « Dépôt
  express »** (entrée) fait face à « Pour toi » (sortie) sur la même ligne :
  son tap ouvre un overlay de saisie qui POST sur `/thoughts` (décharge
  cognitive directe, **sans LLM**, réutilise `record_depot` comme le chemin
  `intent=depot` — zéro divergence). Sélectionner un chip de type
  (souci/idée/note) a un **double rôle** : taguer le prochain dépôt **et**
  lister sous le formulaire les dépôts déjà enregistrés de ce type (`GET
  /thoughts?kind=`, sans LLM) ; chaque entrée ouverte porte un bouton « C'est
  réglé » (`POST /thoughts/{id}/close`). La card « tâches du jour » a été retirée
  (une liste rajoute de la charge mentale) ; l'endpoint `GET /tasks` et son
  overlay restent en place. Mode chat optionnel via
  icône 💬 pour les conversations longues. Le **mode dialogue conserve son
  historique de bulles** : les échanges streamés (`/ask/stream` uniquement —
  ni Siri, ni photos, ni bulle éphémère) sont persistés en SQLite et
  réaffichés datés au reload via `GET /history` (scroll infini avec
  séparateurs de jour, fenêtre glissante `CHAT_HISTORY_RETENTION_DAYS`).
  L'en-tête du chat porte un **toggle « réflexion »** (bouton cerveau, état
  session-only) qui active le reasoning du modèle pour le message envoyé
  (`AskRequest.think` → `process_message_stream` → `chat_stream(think=)`,
  override du défaut `OLLAMA_THINK`) ; un discret « réflexion en cours… »
  s'affiche pendant que le modèle pense avant de streamer.
  **Plus de briefing matin automatique ni de card carburant**
  (intentionnellement, pour ne pas pousser d'info entrante non sollicitée).
  Code sous `frontend/` : **app React 18 + TypeScript + Vite + Tailwind 3**
  (miroir de la stack `domestique-ai`). Sources sous `frontend/src/` (`main.tsx`,
  `App.tsx`, `api/`, `components/`, `hooks/`, `index.css`), PWA (`manifest.json`,
  `sw.js`, icônes) sous `frontend/public/`. Le build Vite produit
  `frontend/dist` (assets hashés → **plus de `?v=N` manuel**), servi par FastAPI
  via `SPAStaticFiles` (`index.html` en `no-store`, catch-all fallback SPA).
- **Profil utilisateur YAML** (`data/profile.yaml`): fichier édité à la main
  décrivant l'utilisateur (identité, famille, travail, voiture, routines,
  préférences). Injecté tel quel dans le system prompt à chaque appel LLM,
  avant le contexte mémoire RAG.
- **Voix Siri**: raccourci iOS "Dis à Copain" qui POST sur `/ask` avec un
  header `X-Source: siri`. Le bot adapte alors son system prompt pour
  produire des réponses TTS-friendly (1-2 phrases, pas de markdown ni
  d'emoji). La variante **`X-Source: siri-conversation`** (boucle Shortcut
  multi-tours) ajoute par-dessus un mode dialogue : pas de re-salutation à
  chaque tour, relance courte si pertinent, clôture brève (implique le
  mode vocal ; le contexte des tours est porté par l'history roulante en
  mémoire). Voir `docs/ios-shortcuts.md`.
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
        │     ├── GET  /             → SPAStaticFiles(frontend/dist) → Safari iOS (PWA React, catch-all)
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
        │     ├── POST /thoughts        → record_depot() → dépôt express (card dashboard, sans LLM) → accusé + loop_size
        │     ├── POST /thoughts/{id}/close → ThoughtManager.close(id) → tap "C'est réglé" (404 si inconnu)
        │     ├── GET  /history       → ChatHistoryManager.page(limit, before_id) → bulles du mode dialogue (scroll infini)
        │     ├── GET  /foryou        → ForYouBuilder.build() → card "Pour toi" (restitution, fail-soft)
        │     ├── GET  /tasks         → TaskManager.list_pending() → overlay tâches PWA (cochage)
        │     ├── POST /tasks/{id}/complete → TaskManager.complete(id)
        │     ├── GET  /budget        → compute_budget() détaillé (transactions + pending) → overlay Budget
        │     ├── GET  /share/courses → restant enveloppe "courses" formaté (phrase prête à partager, raccourci iOS) — 404 si non configurée
        │     ├── POST /expenses      → ExpenseManager.add_punctual/add_income/tick_recurring_once → saisie directe (formulaire PWA, sans LLM)
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
        │     ├── call_with_recall(msg, notes)       → reformule à partir d'extraits mémoire (intent memory)
        │     ├── call_with_recall_stream(…)         → idem, streamé (recall via /ask/stream)
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
        │         (memory = recall : lecture de la mémoire via memory_query)
        │         + TaskMeta / FeedMeta / EventMeta / FuelMeta / WeatherMeta / DepotMeta / ExpenseMeta
        │
        ├── Memory Manager (ChromaDB + nomic-embed-text via Ollama)
        │     ├── store()               → embed + persist the memory_content
        │     ├── store_depot()         → embed + tag {kind=depot, thought_id, thought_kind}
        │     ├── find_similar_depots() → voisins d'un dépôt (where=depot, distances) → boucles + connexions
        │     ├── embed_texts()         → embeddings de textes libres (booster sémantique souci↔évent)
        │     └── retrieve_context()    → top-k chunks pondérés (seuil de distance + boost de récence)
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
        ├── Chat History Manager (SQLite — table `chat_messages`, bot/chat/)
        │     └── add_exchange / page(before_id) / purge_older_than — historise
        │         le mode dialogue (`/ask/stream` uniquement) pour réafficher
        │         les bulles datées côté PWA (fenêtre glissante, purge au boot)
        │
        ├── Restitution des dépôts (card "Pour toi" — bot/thoughts/)
        │     ├── restitution.py → heuristiques pures (select_candidates, is_loop,
        │     │                     _connection_candidates ; types closable_worry/loop/connection/stale_idea)
        │     └── foryou.py      → ForYouBuilder.build (collecte fail-soft +
        │                          rapprochement worry↔évent lexical +
        │                          _gather_similar/_detect_loops/_detect_connections + LLM)
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
        │     ├── NominatimClient    → OSM geocoding (FR, in-memory cache)
        │     └── OverpassClient     → enseigne OSM (amenity=fuel), enrichissement fail-soft
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
| Tests         | pytest + pytest-asyncio (auto mode, 730+ tests)         |
| Quality       | ruff (lint+format) + mypy strict via pre-commit         |
| Interface web | React 18 + TypeScript + Vite + Tailwind 3 (build Vite)   |
| Monitoring    | Sentry SDK (opt-in via `SENTRY_DSN`)                    |
| Push iOS      | Pushover (opt-in via `PUSHOVER_TOKEN` + `PUSHOVER_USER`)|

---

## HTTP endpoints

All endpoints require the `X-API-Key` header matching `settings.api_key`.
Missing or invalid → 403 with a warning logged (source IP included).

| Method | Path               | Body                                                              | Response                                                                                                |
| ------ | ------------------ | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| POST   | `/ask`             | `{ "message": str }` <br>(header `X-Source: siri` → mode vocal ; `siri-conversation` → mode dialogue continu) | `{ "response": str, "intent": str, "refresh_cards": [str] }`                                            |
| POST   | `/ask/stream`      | `{ "message": str }`                                              | SSE `text/event-stream` — frames `data: { "type": "delta"\|"replace"\|"done"\|"error", … }`             |
| POST   | `/ask/image`       | `{ "message": str, "image_b64": str, "media_type": str }`         | `{ "response": str, "intent": str, "refresh_cards": [str] }`                                            |
| GET    | `/notifications`   | —                                                                 | `{ "notifications": [ { "id": int, "text": str, "created_at": str } ] }`                                |
| GET    | `/dashboard`       | —                                                                 | `{ "weather": …, "next_event": …, "today_tasks": […], "unread_notifications": int }`                    |
| GET    | `/news/latest`     | —                                                                 | `{ "markdown": str, "fetched_at": str }`                                                                |
| GET    | `/thoughts`        | `?since=<ISO>&limit=<int>&kind=worry\|idea\|note` (optionnels ; `kind` invalide → 400) | `{ "thoughts": [ { "id": int, "content": str, "kind": str\|null, "created_at": str, "closed": bool } ] }` |
| POST   | `/thoughts`        | `{ "content": str, "kind"?: "worry"\|"idea"\|"note"\|null }`      | `{ "recorded": bool, "thought": {…}, "ack": str }` — dépôt express (card dashboard, sans LLM). 400 si content vide / kind invalide |
| POST   | `/thoughts/{id}/close` | —                                                             | `{ "closed": bool, "thought_id": int }` (idempotent, 404 si id inconnu)                                 |
| GET    | `/history`         | `?limit=<int>&before_id=<int>` (optionnels)                       | `{ "messages": [ { "id": int, "role": str, "content": str, "created_at": str } ], "has_more": bool }`   |
| GET    | `/foryou`          | —                                                                 | `{ "items": [ { "type": str, "message": str, "thought_ids": [int] } ], "fetched_at": str }`             |
| GET    | `/tasks`           | —                                                                 | `{ "tasks": [ { "id": int, "content": str, "due_at": str\|null } ] }` (tâches en cours)                |
| POST   | `/tasks/{task_id}/complete` | —                                                        | tâche marquée terminée (overlay PWA)                                                                    |
| GET    | `/budget`          | —                                                                 | détail du cycle courant : transactions + récurrentes pending (overlay Budget)                           |
| GET    | `/share/courses`   | —                                                                 | `{ "text": str, "label": str, "remaining_eur": float, "allocated_eur": float, "spent_eur": float, "is_overrun": bool, "as_of": str }` — restant enveloppe "courses" prêt à partager (404 si non configurée) |
| POST   | `/expenses`        | `{ "action": "spend"\|"income"\|"tick_recurring", "amount_eur"?, "label"?, "category"?, "occurred_on"?, "shared"?, "recurring_key"?, "starts_cycle"? }` | `{ "recorded": bool, "transaction": {…}\|null }` — saisie directe sans LLM (`recorded:false` = tick déjà pointé) |
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
  "search_query": "query if intent=search, otherwise null",
  "memory_query": "what to look up in memory if intent=memory (recall), otherwise null"
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

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
