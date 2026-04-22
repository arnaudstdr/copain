# copain — Assistant personnel Telegram — CLAUDE.md

## Vue d'ensemble du projet

Bot Telegram mono-utilisateur, tout en langage naturel français. Conçu pour
Arnaud, self-hosted partiellement (services locaux sur Raspberry Pi 5 8 Go,
LLM principal en cloud).

### Capacités actuelles

- **Conversation** avec mémoire sémantique automatique (ChromaDB + embeddings)
- **Tâches + rappels** en langage naturel (SQLite, rappels push Telegram à l'échéance)
- **Recherche web** via SearXNG self-hosted avec résumé FR
- **Flux RSS** : ajout/liste/suppression + résumé des dernières actus à la demande
- **Briefing matinal** automatique chaque jour (heure configurable, défaut 8h) :
  météo locale + tâches du jour + évènements du jour + top 5 actus RSS résumées
- **Analyse photo** : envoi d'image Telegram → vision multimodale du LLM → extraction
  de texte, description de scène, compréhension de graphique, puis routage dans
  le pipeline normal (mémoire/tâche/event selon le contenu)
- **Calendrier iCloud** (CalDAV) : création et listing d'évènements dans n'importe
  quel calendrier iCloud, visible nativement sur iPhone / Apple Watch / Mac
- **Proactivité opt-in** : un job APScheduler tick toutes les N min (défaut 30) et
  peut pousser au plus **une** notif par tick. Deux règles en v1 — alerte pluie
  dans l'heure (Open-Meteo horaire) et rappel RDV ~1 h avant (calendrier iCloud).
  Cinq garde-fous empêchent le spam : feature flag global, fenêtre horaire
  configurable (défaut 8h-21h), budget quotidien max 3, dédup par `event_uid`
  pour les events, cooldown temporel pour la pluie. Désactivé par défaut
  (`PROACTIVITY_ENABLED=false`).

Tout passe par le même pipeline : un LLM décide l'intent via un bloc `<meta>` JSON,
le code exécute les effets de bord, puis renvoie un message texte à l'utilisateur.
Les notifs proactives passent, elles, par un job autonome (pas de LLM ni de routing
`<meta>`) qui écrit dans la table `notification_logs` pour tracer cooldowns et budget.

---

## Architecture

```
Telegram API
     │
     ▼
Bot Python (python-telegram-bot v21, async)
     │
     ├── Middleware sécurité (ALLOWED_USER_ID whitelist)
     │
     ├── Handlers
     │     ├── filters.TEXT  → make_handler       → pipeline standard
     │     └── filters.PHOTO → make_photo_handler → pipeline + images[]
     │
     ├── LLM Client (Ollama — gemma4:31b-cloud multimodal)
     │     ├── call(system, user, images?)  → chat API Ollama
     │     ├── call_with_search(message, results) → relance avec résultats SearXNG
     │     └── chat(messages)              → appel bas niveau
     │
     ├── Parser <meta>
     │     └── Intent ∈ {answer, task, search, memory, feed, event}
     │         + TaskMeta / FeedMeta / EventMeta
     │
     ├── Memory Manager (ChromaDB + nomic-embed-text via Ollama)
     │     ├── store()             → embed + persist le memory_content
     │     └── retrieve_context()  → top-5 chunks pertinents
     │
     ├── Task Manager (SQLite via SQLAlchemy async + aiosqlite)
     │     ├── create / list_pending / complete / delete
     │     └── ReminderScheduler
     │           ├── SQLAlchemyJobStore → rappels one-shot persistés
     │           └── MemoryJobStore     → cron (closures non-sérialisables)
     │
     ├── RSS Manager
     │     ├── FeedManager (SQLAlchemy, table `feeds`)
     │     └── RssFetcher (feedparser via asyncio.to_thread)
     │
     ├── Search Manager
     │     └── SearxngClient (HTTP JSON local)
     │
     ├── iCloud Calendar (CalDAV via lib `caldav`)
     │     ├── ICloudCalendarClient.connect()    → découverte des calendriers
     │     ├── create_event(calendar_name?)      → fuzzy match du calendrier cible
     │     └── list_between / list_today / list_upcoming
     │
     └── Briefing Service (job cron APScheduler)
           ├── OpenMeteoClient (météo Sélestat)
           ├── _today_tasks / _today_events / _rss_block
           └── send_daily → push Telegram à l'heure configurée
```

---

## Stack technique

| Composant          | Choix                                                    |
|--------------------|----------------------------------------------------------|
| Langage            | Python 3.12+ async/await partout                         |
| Bot Telegram       | `python-telegram-bot >= 21`                              |
| LLM                | Ollama → `gemma4:31b-cloud` (multimodal, cloud)          |
| Embeddings         | Ollama → `nomic-embed-text` (local)                      |
| Mémoire vectorielle| ChromaDB (persistance locale)                            |
| ORM                | SQLAlchemy 2 async + aiosqlite                           |
| Scheduler          | APScheduler (SQLAlchemy + Memory jobstores)              |
| Dates              | dateparser (FR) + normalisation midi/minuit              |
| RSS                | feedparser                                               |
| Météo              | Open-Meteo (HTTP, pas de clé)                            |
| Calendrier         | CalDAV via `caldav` + `vobject`                          |
| Recherche web      | SearXNG (instance locale Docker)                         |
| Config             | python-dotenv (.env validé par dataclass Settings)       |
| Logs               | structlog (console en dev, JSON en prod)                 |
| Conteneur          | Docker + Docker Compose                                  |
| Tests              | pytest + pytest-asyncio (mode auto)                      |
| Qualité            | ruff (lint+format) + mypy strict via pre-commit          |

---

## Modèles

| Modèle                | Type        | Où tourne        | Usage                                      |
|-----------------------|-------------|------------------|--------------------------------------------|
| `gemma4:31b-cloud`    | LLM vision  | Ollama Cloud     | Toutes les réponses + analyse des images   |
| `nomic-embed-text`    | Embeddings  | Ollama local Pi  | Vecteurs pour ChromaDB (à la demande)      |

Le LLM est passé en cloud parce que l'inférence locale sur Pi 5 était trop lente.
Conséquence : la majeure partie du budget RAM initial est libérée. Seuls
`nomic-embed-text` (~300 Mo à la demande), ChromaDB/bot (~1 Go) et SearXNG (~0.3 Go)
tournent localement sur le Pi.

---

## Structure du projet

```
copain/
├── CLAUDE.md                    # ce fichier (source de vérité projet)
├── README.md                    # setup utilisateur
├── ROADMAP.md                   # statut des phases d'implémentation
├── .env                         # secrets (jamais commité)
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
│   ├── main.py                  # entrypoint + post_init/post_shutdown PTB
│   ├── handlers.py              # make_handler + make_photo_handler + _handle_*
│   ├── security.py              # is_allowed(update, allowed_user_id)
│   ├── config.py                # Settings dataclass + load_settings()
│   ├── logging_conf.py          # setup structlog
│   │
│   ├── llm/
│   │   ├── client.py            # LLMClient (chat + images base64)
│   │   ├── prompt.py            # SYSTEM_PROMPT_TEMPLATE + build_system_prompt
│   │   └── parser.py            # Meta TypedDict + extract_meta
│   │
│   ├── memory/
│   │   ├── manager.py           # MemoryManager (ChromaDB)
│   │   └── embeddings.py        # Embedder (nomic-embed-text)
│   │
│   ├── tasks/
│   │   ├── manager.py           # TaskManager async
│   │   ├── models.py            # Task + Base DeclarativeBase (partagée)
│   │   └── scheduler.py         # ReminderScheduler (add_reminder + add_cron_job)
│   │
│   ├── rss/
│   │   ├── manager.py           # FeedManager CRUD
│   │   ├── models.py            # Feed (partage Base avec Task)
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
│   │   ├── weather.py           # OpenMeteoClient + HourlyPrecipitation + codes FR
│   │   └── service.py           # BriefingService (agrège + cron)
│   │
│   └── proactivity/
│       ├── models.py            # NotificationLog (partage Base tasks)
│       ├── rules.py             # evaluate_rain + evaluate_upcoming_event (purs)
│       └── service.py           # ProactivityService.tick + garde-fous
│
├── data/                        # volume Docker persisté
│   ├── chroma/
│   ├── tasks.db                 # SQLite partagée tasks + feeds + notification_logs
│   └── scheduler.db             # APScheduler jobs persistés
│
└── tests/                       # pytest-asyncio, tout mocké (pas d'I/O externes)
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
    └── test_proactivity_service.py
```

---

## Variables d'environnement (.env)

```env
# Telegram
TELEGRAM_BOT_TOKEN=                  # requis
ALLOWED_USER_ID=                     # requis, ton user_id Telegram uniquement

# Ollama (client + modèles)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_LLM_MODEL=gemma4:31b-cloud
OLLAMA_EMBED_MODEL=nomic-embed-text

# SearXNG (instance locale)
SEARXNG_BASE_URL=http://localhost:8888

# Chemins de données (montés en volume Docker)
DATA_DIR=./data
CHROMA_DIR=./data/chroma
DB_PATH=./data/tasks.db
SCHEDULER_DB_PATH=./data/scheduler.db

# Fuseau horaire (dateparser + APScheduler + affichage)
TZ=Europe/Paris

# Briefing matinal planifié
BRIEFING_HOUR=8
BRIEFING_MINUTE=0

# Coordonnées pour la météo (Open-Meteo, sans clé API)
HOME_LAT=48.26
HOME_LON=7.45
HOME_CITY=Sélestat

# iCloud Calendar (CalDAV) — App-Specific Password obligatoire (2FA Apple ID)
# Générer sur : https://appleid.apple.com → Connexion et sécurité
ICLOUD_USERNAME=                     # requis, Apple ID
ICLOUD_APP_PASSWORD=                 # requis, format xxxx-xxxx-xxxx-xxxx
ICLOUD_CALENDAR_NAME=Personnel       # nom du calendrier par défaut (fuzzy match)

# Proactivité (notifications poussées sans demande — opt-in strict)
PROACTIVITY_ENABLED=false
PROACTIVITY_WINDOW_START_HOUR=8
PROACTIVITY_WINDOW_END_HOUR=21
PROACTIVITY_DAILY_BUDGET=3
PROACTIVITY_CHECK_INTERVAL_MIN=30
PROACTIVITY_RAIN_COOLDOWN_HOURS=3

# Logs — fichier rotatif JSON persisté dans le volume Docker (5 Mo x 5 backups).
# Mettre vide pour désactiver la persistance fichier (stdout reste actif).
LOG_FILE_PATH=./data/logs/bot.log

# Environnement (dev | prod) — conditionne le format de log structlog
ENV=dev
```

`bot/config.py` charge et valide ces variables. Les variables marquées
« requis » font crasher le démarrage si absentes (`ConfigError` explicite).

---

## System Prompt (routing par bloc `<meta>`)

Le LLM reçoit à chaque appel un system prompt dont la pièce centrale est un
bloc `<meta>` JSON qu'il DOIT inclure en fin de chaque réponse :

```json
{
  "intent": "answer|task|search|memory|feed|event",
  "store_memory": true|false,
  "memory_content": "résumé factuel si store_memory=true, sinon null",
  "task": {
    "content": "description si intent=task, sinon null",
    "due_str": "expression FR si échéance mentionnée, sinon null"
  },
  "feed": {
    "action": "add|list|remove|summarize, sinon null",
    "name": "nom du flux, sinon null",
    "url": "URL si action=add, sinon null"
  },
  "event": {
    "action": "create|list, sinon null",
    "title": "titre si action=create, sinon null",
    "start_str": "expression FR (ex: 'demain midi'), sinon null",
    "end_str": "expression FR si fin précisée, sinon null (durée 1h par défaut)",
    "location": "lieu si mentionné, sinon null",
    "description": "note, sinon null",
    "range_str": "plage si action=list (ex: 'cette semaine'), sinon null",
    "calendar_name": "calendrier cible si précisé (fuzzy match), sinon null"
  },
  "search_query": "requête si intent=search, sinon null"
}
```

Le prompt complet est dans `bot/llm/prompt.py` (`SYSTEM_PROMPT_TEMPLATE`). Il
contient 6 exemples few-shot pour stabiliser le routing de gemma4 (feed et
event, principalement). Deux règles critiques y sont inscrites :

- **Distinction task vs event** : RDV/réunion/meeting AVEC une heure → `event`,
  sinon → `task`.
- **Mots temporels** : le LLM recopie textuellement « midi » / « minuit » dans
  `start_str` ; la normalisation côté code (`_normalize_fr_time_words`) les
  convertit en `12:00` / `00:00` avant `dateparser.parse`.

---

## Logique de traitement d'un message

```python
async def _process(user_text, chat_id, deps, images=None) -> str:
    # 1. Mémoire contextuelle (top-5 via embeddings)
    memory_context = await deps.memory.retrieve_context(user_text)

    # 2. Construit le system prompt (mémoire + historique)
    system = build_system_prompt(memory_context, deps.history)

    # 3. Appel LLM (+ images optionnelles en base64)
    raw = await deps.llm.call(system=system, user=user_text, images=images)

    # 4. Extrait le bloc <meta> + texte propre
    text, meta = extract_meta(raw)

    # 5. Effets de bord selon intent
    await _apply_side_effects(user_text, chat_id, meta, deps)
    # → store memory, create task + reminder scheduler

    # 6. Branches qui relancent le LLM ou substituent le texte
    if meta["intent"] == "search" and meta["search_query"]:
        results = await deps.search.search(...)
        text = await deps.llm.call_with_search(user_text, results)
    elif meta["intent"] == "feed" and meta["feed"]["action"]:
        text = await _handle_feed(...)   # add/list/remove/summarize
    elif meta["intent"] == "event" and meta["event"]["action"]:
        text = await _handle_event(...)  # create (iCloud) / list

    # 7. Historique glissant + retour
    deps.history.extend([f"user: {user_text}", f"assistant: {text}"])
    return text
```

Pour les photos, `make_photo_handler` télécharge le blob Telegram et appelle
le même `_process()` avec `images=[bytes]`. Le LLM multimodal traite caption
+ image dans le même appel.

---

## Scheduler (APScheduler)

Deux jobstores configurés dans `ReminderScheduler` :

- **`default` (SQLAlchemyJobStore)** — rappels de tâches one-shot persistés
  entre redémarrages (`add_reminder(task_id, due_at, chat_id, content)`).
- **`memory` (MemoryJobStore)** — jobs récurrents (cron) dont la fonction est
  une closure non-sérialisable (ex: briefing). Ils sont re-planifiés au startup
  via `add_cron_job(job_id, func, hour, minute)` dans `_post_init`.

Les deux respectent la timezone configurée (`settings.timezone`).

---

## Calendrier iCloud

`ICloudCalendarClient` se connecte à `https://caldav.icloud.com/` via la lib
`caldav` (synchrone, wrappée en `asyncio.to_thread`). Auth par App-Specific
Password.

Au `connect()`, tous les calendriers disponibles sont listés et stockés dans
`self._all_calendars`. La méthode `resolve_calendar(name)` fait un matching
tolérant à 3 niveaux :

1. Match exact
2. Match normalisé (NFC + strip ZWJ `‍` + variation selectors `️` + trim + casefold)
3. Match « contient » sur la version alphanumérique seule

Conséquence : l'utilisateur peut écrire `ICLOUD_CALENDAR_NAME=Personnel` ou
demander « dans le calendrier sport » même si les noms réels sont
`🧘‍♂️ Personnel ` et `🚴‍♂️ Sport ` avec emojis + espaces.

Scope actuel : **create + list**. Pas de delete/modification/récurrence.

---

## Sécurité

**Le bot ne répond qu'à un seul utilisateur.** Vérifié strictement sur chaque
update dans tous les handlers :

```python
if not is_allowed(update, deps.settings.allowed_user_id):
    return  # silencieux, warning loggé
```

Les tentatives d'accès non autorisées sont loggées avec `user_id` + `username`.

---

## Docker Compose

```yaml
services:
  bot:
    build: .
    restart: unless-stopped
    env_file: .env
    network_mode: host          # accès direct à Ollama local sur localhost
    volumes:
      - ./data:/app/data

  searxng:
    image: searxng/searxng:latest
    restart: unless-stopped
    ports:
      - "8888:8080"
    volumes:
      - ./searxng:/etc/searxng
```

Ollama tourne **hors Docker** sur le Pi (accès GPU/NPU ARM) ; il pointe vers
le modèle `cloud` quand le model ID contient le suffixe `-cloud`.

---

## Gestion d'erreurs globale

- Les handlers Telegram wrappent chaque appel dans `try/except` et répondent
  un message générique en cas d'erreur interne.
- Un `Application.add_error_handler(_error_handler)` est enregistré pour
  soft-fail sur `NetworkError` / `TimedOut` (coupures momentanées vers
  `api.telegram.org`), qui sont loggés en warning sans stacktrace. Toute
  autre erreur passe en `log.exception`.
- Le `post_init` tolère un échec de `ICloudCalendarClient.connect()` : il
  logge un warning et laisse le bot démarrer. L'intent `event` renverra
  alors « calendrier indisponible ».

---

## Contraintes hardware (Pi 5 8 Go)

Avec le LLM en cloud, la pression RAM locale est faible :

| Composant             | RAM       |
|-----------------------|-----------|
| ChromaDB + bot Python | ~1 Go     |
| SearXNG               | ~0.3 Go   |
| nomic-embed-text      | ~0.3 Go à la demande |
| **Total**             | **~1.6 Go sur 7 Go dispo** |

Large marge pour ajouter d'autres services (Home Assistant, RSSHub, etc.) si
besoin à l'avenir.

---

## Conventions de code

- **Python 3.12+**, type hints stricts partout (`mypy --strict`)
- **async/await** pour tous les I/O (Telegram, Ollama, ChromaDB, SQLite,
  httpx, caldav en `to_thread`)
- **Gestion d'erreurs explicite**, pas de `bare except`
- **Logs structurés via `structlog`** (pas le `logging` standard), deux
  handlers : stdout (console coloré en dev, JSON en prod selon `ENV`) +
  fichier rotatif JSON `data/logs/bot.log` (5 Mo x 5 backups) si
  `LOG_FILE_PATH` est défini — persistés dans le volume Docker pour
  `grep`/`jq` après coup
- **Variables d'environnement via `python-dotenv`**, jamais de valeurs hardcodées
  hors de `bot/config.py`
- **Tests pytest + pytest-asyncio** (mode auto), dépendances externes mockées —
  la suite tourne sans Ollama/Telegram/iCloud/SearXNG réels
- **Pre-commit** avec `ruff check --fix`, `ruff format`, `mypy --strict` sur `bot/`
- **Base SQLAlchemy partagée** entre modules qui cohabitent dans `tasks.db`
  (tasks + feeds) — importer `Base` depuis `bot.tasks.models`

Commandes utiles via `Makefile` :

```bash
make install   # crée .venv, installe deps dev, installe pre-commit hooks
make run       # lance le bot en foreground
make test      # pytest (aucune I/O externe)
make lint      # ruff check
make format    # ruff format + --fix
make typecheck # mypy strict
make docker-build / docker-up / docker-down
```
