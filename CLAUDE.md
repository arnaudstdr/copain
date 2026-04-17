# Assistant Personnel Telegram — CLAUDE.md

## Vue d'ensemble du projet

Bot Telegram personnel qui sert d'assistant conversationnel au quotidien.
Tout en langage naturel français, pas de commandes slash.
Hébergé sur un Raspberry Pi 5 8 Go en self-hosted.

### Usages principaux
- Prise de notes avec mémoire automatique et recherche sémantique
- Gestion de tâches et rappels en langage naturel
- Recherche web via SearXNG self-hosted
- Conversation générale et suggestions

---

## Architecture

```
Telegram API
     │
     ▼
Bot Python (python-telegram-bot)
     │
     ├── Middleware sécurité (ALLOWED_USER_ID whitelist)
     │
     ├── Memory Manager (ChromaDB + nomic-embed-text via Ollama)
     │     ├── store_message()     → embed + persist tout message mémorable
     │     └── retrieve_context()  → top-5 chunks pertinents par requête
     │
     ├── Task Manager (SQLite via SQLAlchemy)
     │     ├── create_task()
     │     ├── list_tasks()
     │     ├── complete_task()
     │     └── schedule_reminder()  → APScheduler
     │
     ├── Search Manager (SearXNG)
     │     ├── search()            → appel HTTP SearXNG local
     │     └── format_results()    → résumé injecté dans contexte LLM
     │
     └── LLM Client (Ollama — gemma3:4b)
           ├── build_prompt()      → system + mémoire + historique + message
           ├── call()              → appel Ollama API
           └── parse_meta()        → extrait le bloc <meta> JSON
```

---

## Stack technique

```
Langage         Python 3.12+
Bot             python-telegram-bot >= 21.x
LLM             Ollama (gemma3:4b)
Embeddings      Ollama (nomic-embed-text)
Mémoire         ChromaDB (persistance locale)
Base de données SQLite via SQLAlchemy
Scheduling      APScheduler (jobs persistés en DB)
Dates           dateparser (parsing FR)
Recherche web   SearXNG (instance locale, appels HTTP)
Config          python-dotenv (.env)
Conteneur       Docker + Docker Compose
```

---

## Modèles Ollama

| Modèle | Usage | RAM |
|--------|-------|-----|
| `gemma3:4b` | LLM principal, toutes les réponses | ~3.5 Go |
| `nomic-embed-text` | Embeddings pour ChromaDB | ~300 Mo (à la demande) |

Les deux tournent sur le Pi via Ollama. Ne pas utiliser d'autre modèle sans vérifier
que la RAM totale reste sous 7 Go (laisser 1 Go pour l'OS).

---

## Structure du projet

```
telegram-assistant/
├── CLAUDE.md
├── .env                        # secrets (ne jamais commiter)
├── .env.example
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
│
├── bot/
│   ├── __init__.py
│   ├── main.py                 # entrypoint, setup Application telegram
│   ├── handlers.py             # handler message entrant principal
│   ├── security.py             # vérification ALLOWED_USER_ID
│   │
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── client.py           # appels Ollama API
│   │   ├── prompt.py           # construction du system prompt
│   │   └── parser.py           # extraction bloc <meta> JSON
│   │
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── manager.py          # ChromaDB store + retrieve
│   │   └── embeddings.py       # client nomic-embed-text
│   │
│   ├── tasks/
│   │   ├── __init__.py
│   │   ├── manager.py          # CRUD tâches SQLAlchemy
│   │   ├── models.py           # modèles SQLAlchemy
│   │   └── scheduler.py        # APScheduler + rappels Telegram
│   │
│   └── search/
│       ├── __init__.py
│       └── searxng.py          # client HTTP SearXNG
│
├── data/                       # volume Docker persisté
│   ├── chroma/                 # base vectorielle ChromaDB
│   ├── tasks.db                # SQLite
│   └── scheduler.db            # APScheduler jobs
│
└── tests/
    ├── test_memory.py
    ├── test_tasks.py
    └── test_parser.py
```

---

## Variables d'environnement (.env)

```env
# Telegram
TELEGRAM_BOT_TOKEN=your_token_here
ALLOWED_USER_ID=123456789          # ton user_id Telegram uniquement

# Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_LLM_MODEL=gemma3:4b
OLLAMA_EMBED_MODEL=nomic-embed-text

# SearXNG
SEARXNG_BASE_URL=http://localhost:8080

# Chemins données
DATA_DIR=/app/data
CHROMA_DIR=/app/data/chroma
DB_PATH=/app/data/tasks.db
SCHEDULER_DB_PATH=/app/data/scheduler.db
```

---

## System Prompt

Le LLM reçoit ce system prompt à chaque appel. C'est la pièce centrale du routing.

```python
SYSTEM_PROMPT = """
Tu es l'assistant personnel d'Arnaud. Tu communiques en français, de façon
naturelle, concise et directe. Pas de formules de politesse inutiles.

À chaque réponse, tu DOIS inclure en toute fin un bloc entre balises
<meta></meta> contenant un objet JSON valide avec ces champs :

<meta>
{{
  "intent": "answer|task|search|memory",
  "store_memory": true|false,
  "memory_content": "résumé factuel en une phrase si store_memory est true, sinon null",
  "task": {{
    "content": "description de la tâche si intent=task, sinon null",
    "due_str": "expression temporelle extraite du message si présente, sinon null"
  }},
  "search_query": "requête de recherche si intent=search, sinon null"
}}
</meta>

Règles pour store_memory :
- true  → information factuelle, décision, contexte personnel, préférence, rappel important
- false → salutations, remerciements, questions simples sans contenu mémorable

Règles pour intent :
- "task"   → l'utilisateur veut créer une tâche, un rappel, noter quelque chose à faire
- "search" → l'utilisateur veut chercher une info sur le web, une actualité
- "memory" → l'utilisateur cherche dans ses notes passées
- "answer" → tout le reste, réponse directe

--- Contexte mémoire (notes et conversations passées pertinentes) ---
{memory_context}

--- Historique récent de la conversation ---
{recent_history}
"""
```

---

## Logique de traitement d'un message

```python
async def handle_message(message: str) -> str:
    # 1. Récupérer le contexte mémoire pertinent
    memory_context = await memory_manager.retrieve_context(message, top_k=5)

    # 2. Construire et envoyer le prompt
    response_raw = await llm_client.call(
        system=build_system_prompt(memory_context, recent_history),
        user=message
    )

    # 3. Extraire le bloc <meta> et le texte propre
    text, meta = parser.extract_meta(response_raw)

    # 4. Exécuter les actions selon l'intent
    if meta["store_memory"]:
        await memory_manager.store(message, meta["memory_content"])

    if meta["intent"] == "task":
        due_dt = dateparser.parse(meta["task"]["due_str"], languages=["fr"],
                                  settings={"PREFER_DATES_FROM": "future"})
        task = await task_manager.create(meta["task"]["content"], due_dt)
        if due_dt:
            scheduler.add_reminder(task.id, due_dt)

    elif meta["intent"] == "search":
        results = await searxng.search(meta["search_query"])
        # Relancer le LLM avec les résultats injectés dans le contexte
        text = await llm_client.call_with_search(message, results)

    # 5. Retourner le texte propre (sans le bloc <meta>)
    return text
```

---

## Mémoire automatique

Tout message est potentiellement stocké. Le LLM décide via `store_memory`.

```python
# Stockage : embed le memory_content (résumé factuel), pas le message brut
# Cela évite le bruit dans les embeddings

# Retrieval : recherche sémantique sur la collection ChromaDB
# top_k=5, retournés comme blocs de contexte dans le system prompt

# Collection ChromaDB : "personal_memory"
# Metadata stockés : timestamp, original_message (pour debug)
```

---

## Gestion des tâches

```python
# Modèle SQLAlchemy
class Task(Base):
    id: int
    content: str
    due_at: datetime | None
    completed: bool
    created_at: datetime

# APScheduler : JobStore SQLAlchemy pour persistance entre redémarrages
# À l'échéance → envoi d'un message Telegram "⏰ Rappel : {content}"
```

---

## SearXNG

SearXNG tourne en local sur le Pi dans un conteneur Docker séparé.
Le bot fait des appels HTTP JSON sur son API interne.

```python
# GET http://localhost:8080/search?q={query}&format=json&language=fr
# Retourne les N premiers résultats (titre + url + snippet)
# Le bot injecte ces résultats dans le contexte et relance le LLM
# pour obtenir un résumé en français
```

---

## Sécurité

**Le bot ne répond qu'à un seul utilisateur.** Vérification stricte sur chaque update.

```python
# security.py
def is_allowed(update: Update) -> bool:
    return update.effective_user.id == int(os.getenv("ALLOWED_USER_ID"))

# Dans chaque handler :
if not is_allowed(update):
    return  # silencieux, pas de réponse
```

---

## Docker Compose

```yaml
services:
  bot:
    build: .
    restart: unless-stopped
    env_file: .env
    network_mode: host          # accès direct à Ollama sur localhost
    volumes:
      - ./data:/app/data

  searxng:
    image: searxng/searxng:latest
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - ./searxng:/etc/searxng
```

Ollama tourne directement sur le Pi (pas en Docker) pour accéder au GPU/NPU ARM.

---

## Contraintes hardware (Pi 5 8 Go)

- Budget RAM total : 7 Go max utilisables (1 Go réservé OS)
- gemma3:4b chargé en permanence : ~3.5 Go
- nomic-embed-text : chargé à la demande, déchargé après usage
- ChromaDB + bot Python + SearXNG : ~1 Go
- Ne pas ajouter de dépendances lourdes sans vérifier l'empreinte mémoire

---

## Conventions de code

- Python 3.12+, type hints partout
- async/await pour tous les I/O (Telegram, Ollama, ChromaDB, SQLite)
- Gestion d'erreurs explicite, pas de bare except
- Logs structurés via le module `logging` standard
- Tests unitaires dans `tests/` avec pytest
- Variables d'environnement via `python-dotenv`, jamais de valeurs hardcodées
