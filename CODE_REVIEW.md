# Revue de code — copain

_Date : 2026-04-21 · Commit : `64c3c37` · Tag : `0.1.0`_

Revue complète du projet, organisée par gravité décroissante. Chaque point cite
le fichier et la ligne concernés, décrit le risque, et propose un remède.

---

## 🔴 Critique

### 1. Token Telegram sérialisé en clair dans `scheduler.db`

**Fichier** : `bot/tasks/scheduler.py:67-74`

```python
self._scheduler.add_job(
    _send_reminder,
    trigger="date",
    run_date=due_at,
    args=[self._bot_token, chat_id, content],  # ← token picklé sur disque
    id=f"task-{task_id}",
    replace_existing=True,
)
```

Le `SQLAlchemyJobStore` pickle les arguments du job. Conséquence : le
`TELEGRAM_BOT_TOKEN` atterrit dans `data/scheduler.db`, lisible via n'importe
quel backup non chiffré, copie de volume Docker, ou snapshot. Un attaquant qui
récupère ce fichier peut prendre le contrôle total du bot.

**Remède** : ne passer que `chat_id, content, task_id` dans `args` ; laisser
`_send_reminder` récupérer le token via `os.environ["TELEGRAM_BOT_TOKEN"]`. Ou,
plus propre : stocker les rappels dans `tasks.db` et requêter
`due_at <= now` via un cron en mémoire toutes les 30 s.

### 2. Rappels fantômes après complétion ou suppression de tâche

**Fichier** : `bot/tasks/scheduler.py:77-80` (méthode jamais appelée)

`cancel_reminder()` existe mais n'est **jamais invoqué** quand
`TaskManager.complete()` ou `delete()` sont exécutés. Résultat : une tâche
marquée comme faite continue de déclencher un rappel Telegram à l'échéance
initiale. Comportement contre-intuitif et source de confusion.

**Remède** : injecter la référence `ReminderScheduler` dans `TaskManager`, ou
exposer un hook `on_complete`/`on_delete` que `handlers.py` branche.

---

## 🟠 Majeur

### 3. Absence de timeout sur le client Ollama

**Fichiers** : `bot/llm/client.py:30`, `bot/memory/embeddings.py:22`

```python
self._client = AsyncClient(host=base_url)
```

Aucun timeout configuré. Si Ollama cloud latence ou freeze, le handler Telegram
attend indéfiniment, bloque le slot de traitement, et les messages suivants
s'accumulent. Avec `gemma4:31b-cloud`, une latence réseau momentanée peut figer
le bot plusieurs minutes.

**Remède** :

```python
import httpx
self._client = AsyncClient(host=base_url, timeout=httpx.Timeout(60.0))
```

### 4. Pas de timeout côté CalDAV (appels bloquants dans `to_thread`)

**Fichier** : `bot/calendar/client.py:69-75`

Si `caldav.icloud.com` est lent, le thread reste alloué dans le pool par
défaut de `asyncio`. Sous plusieurs listings enchaînés, on sature.

**Remède** : passer `timeout=10` à `DAVClient` (supporté par la lib `caldav`).

### 5. Deux engines SQLAlchemy sur le même fichier `tasks.db`

**Fichiers** : `bot/tasks/manager.py:20-23`, `bot/rss/manager.py:33-36`

Deux `create_async_engine("sqlite+aiosqlite:///tasks.db")` coexistent avec
leurs pools respectifs. SQLite a un verrou écrivain global ; deux pools
indépendants peuvent se renvoyer `database is locked` sous charge. En mono-user
ça passe aujourd'hui, mais c'est fragile et silencieux à l'erreur.

**Remède** : un seul `AsyncEngine` partagé injecté dans `TaskManager` et
`FeedManager`, ou au minimum activer le WAL à l'init :

```python
async with self._engine.begin() as conn:
    await conn.execute(text("PRAGMA journal_mode=WAL"))
```

### 6. Exceptions brutes remontées à l'utilisateur

**Fichier** : `bot/handlers.py:320-322`, `336-337`

```python
except Exception as exc:
    log.error("calendar_create_failed", error=str(exc))
    return f"Désolé, je n'ai pas pu créer l'évènement : {exc}"
```

Deux problèmes :

- `except Exception` est trop large (masque les bugs).
- Le message rendu à l'utilisateur peut exposer URLs, stacktraces caldav,
  chemins internes. Mono-user donc non-critique, mais mauvaise pratique.

**Remède** : catcher explicitement `ICloudCalendarError` et retourner un
message générique. Conserver `log.exception()` pour la trace complète côté
logs.

### 7. `deque(maxlen=...)` non utilisée

**Fichiers** : `bot/main.py:94`, `bot/handlers.py:167-168`

```python
# main.py
history=deque(),                          # ← sans maxlen

# handlers.py
while len(deps.history) > MAX_HISTORY:    # pop manuel
    deps.history.popleft()
```

Le pop manuel fonctionne, mais une exception entre le `append` et la boucle
laisserait la deque non bornée. `deque(maxlen=MAX_HISTORY)` fait la troncature
atomiquement.

**Remède** : `history=deque(maxlen=MAX_HISTORY)` dans `main.py`, supprimer la
boucle dans `_process`.

---

## 🟡 Mineur

### 8. LIKE pattern injection (faible impact)

**Fichier** : `bot/rss/manager.py:70-72`

```python
stmt = select(Feed).where(
    or_(Feed.name == name_or_id, Feed.name.ilike(f"%{name_or_id}%"))
)
```

`name_or_id` peut contenir `%` ou `_` qui changent la sémantique du match.
SQLAlchemy paramétrise (donc pas de SQL injection), mais un nom `"zd%"` match
n'importe quoi commençant par `"zd"`. Mono-user donc bénin.

**Remède** : `sqlalchemy.sql.elements.escape_like(name_or_id)` ou
échappement manuel des `%`/`_`.

### 9. `FeedManager.count()` en O(N) au lieu de O(1)

**Fichier** : `bot/rss/manager.py:96-99`

```python
result = await session.execute(select(Feed))
return len(result.scalars().all())
```

Charge toutes les lignes en mémoire pour les compter.

**Remède** :

```python
from sqlalchemy import func
result = await session.execute(select(func.count()).select_from(Feed))
return result.scalar_one()
```

### 10. `DEFAULT_FEEDS` hardcodé dans `main.py`

**Fichier** : `bot/main.py:34-37`

Couplage code/config. Devrait être une entrée `.env` (liste `;`-séparée)
ou un YAML optionnel, pour ne pas redéployer l'image pour changer un flux.

### 11. Prompt système très gras envoyé à chaque tour

**Fichier** : `bot/llm/prompt.py`

~6 000 caractères + 6 exemples few-shot envoyés à chaque message. Sur Ollama
cloud, c'est de la bande passante et de la latence. Un modèle stabilisé
(`gemma4:31b-cloud`) n'a pas besoin d'autant d'exemples après validation.
Élaguer pourrait économiser 30-40 % de tokens par appel.

### 12. SearXNG potentiellement exposé en `network_mode: host`

**Fichier** : `docker-compose.yml`

Le bot tourne en `network_mode: host` → il accède à SearXNG via
`localhost:8888`. SearXNG (container séparé) bind `:8888` sur toutes les
interfaces. Si le Pi est exposé (port forwarding, IPv6 public, Tailscale mal
configuré), **l'instance SearXNG devient un open proxy**.

**Remède** : `ports: - "127.0.0.1:8888:8080"` pour SearXNG, ou sortir le bot
de `network_mode: host` et faire communiquer les deux en réseau Docker
interne.

### 13. Pas de `.dockerignore`

Le contexte build inclut `.git`, `.venv`, `tests`, `.mypy_cache`… Build lent.
Même si `COPY bot/` limite l'image finale, le transfert de contexte est
gaspillé.

**Remède** : créer un `.dockerignore` explicite (`.git`, `.venv`,
`__pycache__`, `tests/`, `.mypy_cache`, `.ruff_cache`, `.pytest_cache`,
`data/`).

### 14. Dockerfile : pas de multi-stage, pas de `HEALTHCHECK`

Pas bloquant pour un side-project mono-user, mais standard pour un service
long-running. Un `HEALTHCHECK` permettrait à Docker de redémarrer
proactivement en cas de freeze.

### 15. `telegram.Bot(token=...)` reconstruit à chaque rappel et chaque briefing

**Fichiers** : `bot/tasks/scheduler.py:32`, `bot/briefing/service.py:84`

Chaque rappel instancie un `telegram.Bot` + client `httpx`. Coût minime mais
inélégant. Un `Bot` singleton partagé ferait mieux (et règle le point 1 en
prime si on cesse de passer le token en argument du job).

### 16. `try/except Exception` dans `_handle_event` et `_handle_feed`

Trop large — masque les bugs de programmation. Préférer les exceptions
métier (`ICloudCalendarError`, `SearxngError`, `FeedAlreadyExists`).

### 17. `VALID_INTENTS` / `VALID_FEED_ACTIONS` / `VALID_EVENT_ACTIONS` dupliqués

**Fichier** : `bot/llm/parser.py:11-20`

Les `Literal[...]` et les `frozenset` listent les mêmes valeurs deux fois.
Risque de désynchronisation lors de l'ajout d'un intent.

**Remède** :

```python
from typing import get_args
VALID_INTENTS: frozenset[str] = frozenset(get_args(Intent))
```

### 18. Tests : pas de couverture du pipeline `_process` complet

Il y a des tests unitaires pour `_parse_due`, `extract_meta`, `MemoryManager`,
`TaskManager` — mais aucun test intégré qui fait
LLM mock → parser → side_effects → scheduler. C'est pourtant le cœur du bot.

**Remède** : un `test_handlers_process.py` avec tous les services mockés
(AsyncMock) qui vérifie les branches task/search/feed/event.

### 19. Pas de CI

Les hooks pre-commit sont locaux. Un simple `.github/workflows/ci.yml` qui
lance `make lint typecheck test` sur chaque push évite de tagger en prod du
code cassé (ce qui a failli arriver avec `0.1.0`).

---

## 🟢 Points forts

- **Architecture claire**, responsabilités bien séparées
  (`llm`/`memory`/`tasks`/`rss`/`search`/`calendar`/`briefing`), injection via
  `BotDeps`.
- **Type hints stricts** (`mypy --strict`), usage correct de `TypedDict`,
  `Literal`, `frozenset`, `TYPE_CHECKING` pour les imports circulaires.
- **Parsing robuste du bloc `<meta>`** avec validation explicite et
  `MetaParseError` → pas de crash silencieux.
- **Gestion timezone propre** : `ZoneInfo`, `RETURN_AS_TIMEZONE_AWARE`,
  `DateTime(timezone=True)` — le piège classique
  dateparser/APScheduler/UTC-container est bien évité.
- **Matching tolérant calendriers iCloud** (ZWJ, variation selectors,
  casefold, trim) très bien pensé.
- **Error handler PTB** distingue `NetworkError`/`TimedOut` du reste — évite
  le bruit dans les logs.
- **Sécurité mono-user** : `is_allowed()` appelé sur chaque handler, logs
  d'accès refusés avec user_id + username.
- **MemoryJobStore pour les closures** + SQLAlchemyJobStore pour les jobs
  sérialisables : bon compromis pour mixer cron et one-shot.
- **Structlog partout**, pas de `print`, pas de `logging.getLogger` direct.
- **Tests async bien mockés**, aucune dépendance externe réseau dans la
  suite (`pytest` tourne hors ligne).
- **Documentation (CLAUDE.md, README, ROADMAP)** très détaillée et à jour.

---

## Top 5 priorisé

| # | Sévérité | Action                                                          |
|---|----------|-----------------------------------------------------------------|
| 1 | 🔴       | Ne plus pickler le token Telegram dans `scheduler.db`           |
| 2 | 🔴       | Câbler `cancel_reminder` sur complétion/suppression de tâche    |
| 3 | 🟠       | Timeouts explicites sur Ollama + CalDAV                         |
| 4 | 🟠       | Engine SQLAlchemy partagé + activation du WAL                   |
| 5 | 🟡       | `.dockerignore` + CI GitHub Actions (`make lint typecheck test`) |
