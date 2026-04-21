# Roadmap — copain

Suivi d'implémentation des extensions au-delà du socle initial (mémoire, tâches, recherche).
Le plan détaillé de chaque phase est dans `~/.claude/plans/lis-claude-md-pour-cr-er-starry-flamingo.md`.

---

## Phase 1 — RSS + résumé à la demande ✅

Socle pour le briefing matinal. Flux stockés en SQLite (table `feeds`), CRUD en langage naturel.

- [x] `bot/rss/models.py` — modèle `Feed` (id, url, name, category, enabled, created_at)
- [x] `bot/rss/manager.py` — `FeedManager` async (add, list, remove, toggle, get, count)
- [x] `bot/rss/fetcher.py` — wrapper `feedparser` (via `asyncio.to_thread`)
- [x] `bot/llm/parser.py` — ajout intent `feed` + sous-objet `feed {action, name, url}`
- [x] `bot/llm/prompt.py` — règles + 3 exemples few-shot
- [x] `bot/handlers.py` — branche `intent=feed` (add / list / remove / summarize)
- [x] `bot/main.py` — `FeedManager` + `RssFetcher` dans `BotDeps` + seed par défaut
- [x] `tests/test_feeds.py` + extension `tests/test_parser.py`
- [x] Commit atomique

**Sources de départ** : The Verge (`https://www.theverge.com/rss/index.xml`), ZDNet (`https://www.zdnet.com/news/rss.xml`)

---

## Phase 2 — Briefing matinal planifié (consomme Phase 1) ✅

Push Telegram quotidien à 8h : météo Sélestat + tâches du jour + top 5 items RSS résumés.

- [x] `bot/briefing/weather.py` — client Open-Meteo (pas de clé API)
- [x] `bot/briefing/service.py` — `BriefingService` (agrège + envoie)
- [x] `bot/tasks/scheduler.py` — helper `add_cron_job` + MemoryJobStore
- [x] `bot/config.py` — `BRIEFING_HOUR`, `BRIEFING_MINUTE`, `HOME_LAT`, `HOME_LON`, `HOME_CITY`
- [x] `.env.example` — documenter les 5 variables
- [x] `bot/main.py` — scheduling dans `_post_init`, `BriefingService` dans `BotDeps`
- [x] `tests/test_briefing.py`
- [x] Commit atomique

**Coordonnées Sélestat** : 48.26°N, 7.45°E | Timezone : Europe/Paris

---

## Phase 3 — Vision multimodale (photo) ✅

Photo Telegram → `gemma4:31b-cloud` analyse l'image (OCR + description + objets + graphiques) → pipeline standard (mémoire/tâche/search selon contenu).

Approche révisée : plus de Tesseract, le modèle multimodal fait tout (texte + scènes + graphiques).

- [x] `bot/llm/client.py` — `call(..., images=[bytes])` avec encodage base64 pour Ollama
- [x] `bot/llm/prompt.py` — règles pour les images (intent pertinent selon contenu)
- [x] `bot/handlers.py` — `make_photo_handler` + `_process(images=...)`
- [x] `bot/main.py` — `MessageHandler(filters.PHOTO, ...)`
- [x] `tests/test_llm_client.py` — encodage base64 + chat
- [x] Commit atomique

---

## Phase 4 — Transcription vocale ❌ (abandonnée)

**Décision** : remplacée par la dictée native iOS/Telegram côté client. Aucun process supplémentaire à faire tourner sur le Pi, économie de 300 Mo de RAM, pas de latence de transcription, FR pris en charge nativement par Apple Intelligence/clavier iOS. Le bot reçoit directement du texte, rien à coder.

---

## Phase 5 — iCloud Calendar (CalDAV) ✅

Intent `event` distinct de `task` : les RDV vont directement dans le calendrier iCloud via CalDAV, visibles sur iPhone/Apple Watch/Mac en natif. Les tâches locales (avec rappels Telegram) restent inchangées.

- [x] `bot/calendar/models.py` — dataclass `CalendarEvent`
- [x] `bot/calendar/client.py` — `ICloudCalendarClient` async (connect, create_event, list_between, list_today, list_upcoming)
- [x] `bot/llm/parser.py` — intent `event` + `EventMeta` (action create/list)
- [x] `bot/llm/prompt.py` — règle + 2 exemples few-shot
- [x] `bot/handlers.py` — `_handle_event` (create/list) + `_parse_range`
- [x] `bot/main.py` — client dans `BotDeps`, connect tolérant dans `_post_init`
- [x] `bot/briefing/service.py` — 4e section « Évènements du jour »
- [x] `bot/config.py` — `ICLOUD_USERNAME`/`ICLOUD_APP_PASSWORD`/`ICLOUD_CALENDAR_NAME`
- [x] `.env.example` — documente App-Specific Password
- [x] `tests/test_calendar.py` + extension parser & briefing
- [x] Commit atomique

**Setup utilisateur** : créer un App-Specific Password sur appleid.apple.com (obligatoire avec 2FA), renseigner les 3 variables dans `.env`.

**Scope v1** : create + list uniquement. Delete, modification, récurrence, conflits, Rappels Apple (VTODO) → itérations suivantes.

---

## Phase 6 — Proactivité v1 ✅

Le bot peut pousser des messages non sollicités quand l'info a une valeur immédiate, avec garde-fous stricts pour ne jamais spammer. Opt-in via `PROACTIVITY_ENABLED=true`.

Règles de la v1 (2) :

1. **Pluie dans l'heure** — seuils Open-Meteo `mm ≥ 0.3` ou `probabilité ≥ 60 %` sur l'heure courante/suivante.
2. **RDV calendrier dans ~1 h** — event démarrant entre 45 et 75 min, notifié une seule fois par UID.

Garde-fous, dans l'ordre du tick :

1. Feature flag `PROACTIVITY_ENABLED` (défaut `false`).
2. Fenêtre horaire `[PROACTIVITY_WINDOW_START_HOUR, PROACTIVITY_WINDOW_END_HOUR[` (défaut 8-21).
3. Budget quotidien `PROACTIVITY_DAILY_BUDGET` (défaut 3) calculé depuis minuit local.
4. Dédup : par `event_uid` pour les events, cooldown `PROACTIVITY_RAIN_COOLDOWN_HOURS` pour la pluie.
5. Priorité event > pluie (1 seule notif par tick).

- [x] `bot/briefing/weather.py` — `HourlyPrecipitation` + `get_hourly_precipitation()`
- [x] `bot/tasks/scheduler.py` — `add_interval_job()` (MemoryJobStore)
- [x] `bot/proactivity/models.py` — `NotificationLog` (partage `Base`)
- [x] `bot/proactivity/rules.py` — `evaluate_rain` + `evaluate_upcoming_event` (purs)
- [x] `bot/proactivity/service.py` — `ProactivityService.tick()` avec les 5 garde-fous
- [x] `bot/config.py` — 6 vars `PROACTIVITY_*` + helper `_env_bool`
- [x] `bot/main.py` — câblage interval job conditionnel + import du module pour `metadata`
- [x] `.env.example` — bloc Proactivité documenté
- [x] Tests : `test_weather`, `test_scheduler_interval`, `test_proactivity_*`, `test_config`

---

## Budget RAM cumulé (Pi 5 8 Go — 7 Go utilisables)

| Composant            | RAM    |
|----------------------|--------|
| LLM (gemma4:31b-cloud) | 0 Go (cloud) |
| ChromaDB + bot       | 1.0 Go |
| SearXNG              | 0.3 Go |
| **Total estimé**     | **~1.3 Go** |

Le passage à un modèle cloud pour le LLM principal a libéré ~3.5 Go et rendu inutiles
les features qui nécessitaient de la RAM supplémentaire (Whisper, Tesseract). La vision
photo est gérée par le même modèle cloud multimodal.

---

## Historique

| Date       | Commit    | Description                                                    |
|------------|-----------|----------------------------------------------------------------|
| 2026-04-17 | `69c983e` | first commit — scaffolding complet                             |
| 2026-04-17 | `2745b0f` | change url searxng (port 8888)                                 |
| 2026-04-17 | `6c4dd56` | fix: structlog LoggerFactory + async post_init/post_shutdown   |
| 2026-04-17 | `637969b` | fix: timezone aware dateparser + APScheduler                   |
| 2026-04-21 | `1d1af84` | feat(rss): Phase 1 — flux RSS avec intent=feed                |
| 2026-04-21 | `533387f` | feat(briefing): Phase 2 — météo + tâches + RSS à 8h           |
| 2026-04-21 | `6afdfd8` | feat(vision): Phase 3 — analyse photo via gemma4:31b-cloud    |
| 2026-04-21 | _doc_     | docs: Phase 4 abandonnée (dictée native iOS/Telegram)         |
| 2026-04-21 | _Phase 5_ | feat(calendar): iCloud CalDAV, intent=event, briefing étendu  |
| 2026-04-21 | _Phase 6_ | feat(proactivity): pluie + rappel RDV 1h avant, garde-fous    |
