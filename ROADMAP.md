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
