# copain — assistant Telegram personnel

Bot Telegram mono-utilisateur en langage naturel français, hébergé sur Raspberry Pi 5.
Stack : Python 3.12 async, python-telegram-bot, Ollama (`gemma3:4b` + `nomic-embed-text`),
ChromaDB, SQLAlchemy + APScheduler, SearXNG.

Les spécifications détaillées (architecture, system prompt, structure) sont dans [`CLAUDE.md`](./CLAUDE.md).

## Setup local (dev)

```bash
cp .env.example .env      # puis éditer .env avec le vrai token + ton user_id
make install              # crée .venv et installe les deps
make test                 # lance la suite (aucun service externe requis, tout est mocké)
make lint typecheck       # qualité de code
```

## Lancement

```bash
make run                  # lance le bot en foreground (nécessite Ollama + SearXNG réels)
```

## Déploiement Docker (Pi 5)

```bash
make docker-build
make docker-up
```

Ollama doit tourner **hors Docker** sur le Pi pour accéder au GPU/NPU ARM.

## Sécurité

Le bot ne répond qu'à l'utilisateur Telegram dont l'identifiant correspond à `ALLOWED_USER_ID`
dans `.env`. Tout autre update est ignoré silencieusement et loggé en warning.
